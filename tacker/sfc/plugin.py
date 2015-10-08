# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# All Rights Reserved.
#
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# @author: Tim Rozet, Red Hat Inc.


import copy
import eventlet
import inspect

from oslo_config import cfg
from sqlalchemy.orm import exc as orm_exc

from tacker.api.v1 import attributes
from tacker.common import driver_manager
from tacker import context as t_context
from tacker.db.sfc import sfc_db
from tacker.extensions import sfc
from tacker.openstack.common import excutils
from tacker.openstack.common import log as logging
from tacker.plugins.common import constants
import json

LOG = logging.getLogger(__name__)


class SFCPlugin(sfc_db.SFCPluginDb):
    """SFCPlugin which supports SFC framework
    """
    OPTS = [
        cfg.ListOpt(
            'infra_driver', default=['opendaylight'],
            help=_('Hosting device drivers sfc plugin will use')),
    ]
    cfg.CONF.register_opts(OPTS, 'sfc')
    supported_extension_aliases = ['sfc']

    def __init__(self):
        super(SFCPlugin, self).__init__()
        self._pool = eventlet.GreenPool()
        self._device_manager = driver_manager.DriverManager(
            'tacker.sfc.drivers',
            cfg.CONF.sfc.infra_driver)
        self.sff_counter = 1

    @staticmethod
    def find_ovs_br(sf_id, network_map):
        """
        :param sf_id: info to ID an sf, for example neutron port id
        :param network_map: ovsdb network topology list
        :return: bridge_dict: br_name, ovs_ip, ovs_port key-values
        """
        # trozet better way to traverse this is to use json lib itself
        # for now this was quicker to write
        bridge_dict = dict()
        for net in network_map:
            if 'node' in net:
                for node_entry in net['node']:
                    if 'termination-point' in node_entry:
                        for endpoint in node_entry['termination-point']:
                            if 'ovsdb:interface-external-ids' in endpoint:
                                for external_id in endpoint['ovsdb:interface-external-ids']:
                                    if 'external-id-value' in external_id:
                                        if external_id['external-id-value'] == sf_id:
                                            print 'Found!'
                                            print node_entry['ovsdb:bridge-name']
                                            bridge_dict['br_name'] = node_entry['ovsdb:bridge-name']
                                            break
                                        else:
                                            print 'Not Found'
                if 'br_name' in bridge_dict:
                    for node_entry in net['node']:
                        if 'ovsdb:connection-info' in node_entry:
                            bridge_dict['ovs_ip'] = node_entry['ovsdb:connection-info']['remote-ip']
                            bridge_dict['ovs_port'] = node_entry['ovsdb:connection-info']['remote-port']
                            break
        if all(key in bridge_dict for key in ('br_name', 'ovs_ip', 'ovs_port')):
            return bridge_dict

        return

    def locate_ovs_to_sf(self, sfs_dict, driver_name):
        """
        :param sfs_dict: dictionary of SFs by id to network id (neutron port id)
        :param driver_name: name of SDN driver
        :return: dictionary mapping sfs to bridge name
        """
        # get topology
        try:
            network = self._device_manager.invoke(
                driver_name, 'list_network_topology')
        except Exception:
            LOG.exception(_('Unable to get network topology'))
            return

        if network is None:
            return

        LOG.debug(_('Network is %s'), network)

        # br_mapping key is nested dict with br_name as first key
        br_mapping = dict()

        # make extensible to other controllers
        if driver_name == 'opendaylight':
            network_map = network['network-topology']['topology']
            # look to see if vm_id exists in network dict
            for sf in sfs_dict:
                br_dict = self.find_ovs_br(sfs_dict[sf], network_map)
                LOG.debug(_('br_dict from find_ovs %s'), br_dict)
                if br_dict is not None:
                    br_name = br_dict['br_name']
                    if br_name in br_mapping:
                        br_mapping[br_name]['sfs'] = [sf]+br_mapping[br_name]['sfs']
                    else:
                        br_mapping[br_name] = dict()
                        br_mapping[br_name]['sfs'] = [sf]
                        br_mapping[br_name]['ovs_ip'] = br_dict['ovs_ip']
                        br_mapping[br_name]['sff_name'] = 'sff' + str(self.sff_counter)
                        self.sff_counter += 1
                else:
                    LOG.debug(_('Could not find OVS bridge for %s'), sf)

        return br_mapping

    def create_sff_json(self, bridge_mapping, sfs_dict):
        """
        Creates JSON request body for ODL SFC
        :param bridge_mapping: dictionary of sf to ovs bridges
        :return: dictionary with formatted fields
        """
        dp_loc = 'sf-data-plane-locator'
        sff_dp_loc = {'name': '',
                      'data-plane-locator':
                          {
                          'transport': 'service-locator:vxlan-gpe',
                          'port': '',
                          'ip': ''
                          }
                      }
        sf_template = {'name': '',
                       'type': ''
                       }
        sff_sf_dp_loc = {'service-function-forwarder-ovs:ovs-bridge': '',
                         'transport': 'service-locator:vxlan-gpe',
                         'port': '',
                         'ip': ''
                         }

        sff_list = []
        # build dict for each bridge
        for br in bridge_mapping.keys():
            # create sff data-plane locator
            temp_sff_dp_loc = sff_dp_loc.copy()
            temp_sff_dp_loc['name'] = bridge_mapping[br]['sff_name']
            temp_sff_dp_loc['data-plane-locator']['port'] = '6633'
            temp_sff_dp_loc['data-plane-locator']['ip'] = bridge_mapping[br]['ovs_ip']
            # temp_sff_dp_loc['service-function-forwarder-ovs:ovs-bridge'] = br
            temp_bridge_dict = {'bridge-name': br}
            sf_dicts = list()
            for sf in bridge_mapping[br]['sfs']:
                # build sf portion of dict
                temp_sf_dict = sf_template.copy()
                temp_sf_dict['name'] = sfs_dict[sf]['name']
                temp_sf_dict['type'] = sfs_dict[sf]['type']
                # build sf data-plane locator
                temp_sff_sf_dp_loc = sff_sf_dp_loc.copy()
                temp_sff_sf_dp_loc['service-function-forwarder-ovs:ovs-bridge'] = temp_bridge_dict
                # trozet hardcoding first data-plane-locator index
                temp_sff_sf_dp_loc['port'] = sfs_dict[sf][dp_loc][0]['port']
                temp_sff_sf_dp_loc['ip'] = sfs_dict[sf][dp_loc][0]['ip']

                temp_sf_dict['sff-sf-data-plane-locator'] = temp_sff_sf_dp_loc
                sf_dicts.append(temp_sf_dict)

            # combine sf list into sff dict
            temp_sff = dict({'name': temp_sff_dp_loc['name']}.items()
                            + {'sff-data-plane-locator': [temp_sff_dp_loc]}.items()
                            + {'service-function-dictionary': sf_dicts}.items())
            sff_list.append(temp_sff)

        sff_dict = {'service-function-forwarder': sff_list}
        sffs_dict = {'service-function-forwarders': sff_dict}
        LOG.debug(_('SFFS dictionary output is %s'), sffs_dict)
        return sffs_dict

    def _create_sfc(self, context, sfc):
        """
        :param context:
        :param sfc: dictionary of kwargs from REST request
        :return: dictionary of created object?
        """
        sfc_dict = sfc['sfc']
        LOG.debug(_('chain_dict %s'), sfc_dict)

        sfc_dict = self._create_sfc_pre(context, sfc)
        sfc_id = sfc_dict['id']
        # Default driver for SFC is opendaylight
        if 'infra_driver' not in sfc_dict:
            infra_driver = 'opendaylight'
        else:
            infra_driver = sfc_dict['infra_driver']

        dp_loc = 'sf-data-plane-locator'
        sfs_json = dict()
        sf_net_map = dict()
        # Required info for ODL REST call
        # For now assume vxlan and nsh aware
        for sf in sfc_dict['attributes']:
            sf_json = dict()
            sf_json[dp_loc] = list()
            dp_loc_dict = dict()
            sf_id = sf
            sf_json['name'] = sf
            dp_loc_dict['name'] = 'vxlan'
            dp_loc_dict['ip'] = sfc_dict['attributes'][sf]['ip']

            if 'port' in sfc_dict['attributes'][sf].keys():
                dp_loc_dict['port'] = sfc_dict['attributes'][sf]['port']
            else:
                dp_loc_dict['port'] = '6633'

            dp_loc_dict['transport'] = 'service-locator:vxlan-gpe'
            # trozet how do we get SFF?
            # may need to ask ODL to find OVS attached to this VNF
            # then create the SFF
            # since this is a chicken and egg problem between SFF, and SF creation
            # we give a dummy value then figure out later
            dp_loc_dict['service-function-forwarder'] = 'dummy'
            sf_json['nsh-aware'] = 'true'
            sf_json['ip-mgmt-address'] = sfc_dict['attributes'][sf]['ip']
            sf_json['type'] = "service-function-type:%s" % (sfc_dict['attributes'][sf]['type'])
            sf_json[dp_loc].append(dp_loc_dict)

            # concat service function json into full dict
            sfs_json = dict(sfs_json.items() + {sf_id: sf_json}.items())

            # map sf id to network id (neutron port)
            sf_net_map[sf] = sfc_dict['attributes'][sf]['neutron_port_id']

        LOG.debug(_('dictionary for sf_net_map:%s'), sf_net_map)
        LOG.debug(_('dictionary for sf json:%s'), sfs_json)

        # Locate OVS, ovs_mapping will be a nested dict
        # first key is bridge name, secondary keys sfs list, ovs_ip, sff_name
        ovs_mapping = self.locate_ovs_to_sf(sf_net_map, infra_driver)

        LOG.debug(_('OVS MAP:%s'), ovs_mapping)

        # Go back and update sf SFF
        for br_name in ovs_mapping.keys():
            for sf_id in ovs_mapping[br_name]['sfs']:
                # sfs_json[sf_id]['service-function-forwarder'] = ovs_mapping[br_name]['sff_name']
                sfs_json[sf_id]['sf-data-plane-locator'][0]['service-function-forwarder'] = ovs_mapping[br_name]['sff_name']
                LOG.debug(_('SF updated with SFF:%s'), ovs_mapping[br_name]['sff_name'])
        # try to create SFs
        service_functions_json = {'service-functions': {}}
        service_functions_json['service-functions'] = {'service-function': list()}
        for (x, y) in sfs_json.items():
            service_functions_json['service-functions']['service-function'].append(y)

        LOG.debug(_('json request formatted sf json:%s'), json.dumps(service_functions_json))
        try:
            sfc_result = self._device_manager.invoke(
                infra_driver, 'create_sfs', sfs_json=service_functions_json)
        except Exception:
            LOG.exception(_('Unable to create SFs'))
            return

        # build SFF json
        sff_json = self.create_sff_json(ovs_mapping, sfs_json)
        # try to create SFFs
        LOG.debug(_('json request formatted sf json:%s'), json.dumps(sff_json))
        try:
            sff_result = self._device_manager.invoke(
                infra_driver, 'create_sff', sff_json=sff_json)
        except Exception:
            LOG.exception(_('Unable to create SFFs'))
            return

        # try to create SFC

        # try to create SFP
        # trozet FIXME
        instance_id = None

        # try to create RSP

        # if we get to this point we know ODL is at least trying to create items
        # we use instance ID from ODL SFC create as the instance_id
        if instance_id is None:
            self._create_sfc_post(context, sfc_id, None, None,
                                  sfc_dict)
            return

        sfc_dict['instance_id'] = instance_id
        return sfc_dict

    def create_sfc(self, context, sfc):
        sfc_dict = self._create_sfc(context, sfc)

        def create_sfc_wait():
            self._create_sfc_wait(context, sfc_dict)

        self.spawn_n(create_sfc_wait)
        return sfc_dict

    def _create_sfc_wait(self, context, sfc_dict):
        driver_name = self._infra_driver_name(sfc_dict)
        sfc_id = sfc_dict['id']
        instance_id = self._instance_id(sfc_dict)

        try:
            self._device_manager.invoke(
                driver_name, 'create_wait', plugin=self, context=context,
                sfc_dict=sfc_dict, sfc_id=instance_id)
        except sfc.SFCCreateWaitFailed:
            instance_id = None
            del sfc_dict['instance_id']

        self._create_sfc_post(
            context, sfc_id, instance_id, sfc_dict)
        if instance_id is None:
            return

        new_status = constants.ACTIVE

        sfc_dict['status'] = new_status
        self._create_sfc_status(context, sfc_id, new_status)

    # TODO fill in update and delete
