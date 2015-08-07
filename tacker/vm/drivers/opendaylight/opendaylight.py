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
# shamelessly many codes are stolen from gbp simplechain_driver.py

import sys
import time
import yaml
import requests
import json

from keystoneclient.v2_0 import client as ks_client
from oslo_config import cfg

from tacker.common import exceptions
from tacker.common import log
from tacker.openstack.common import jsonutils
from tacker.openstack.common import log as logging
from tacker.vm.drivers import abstract_driver


LOG = logging.getLogger(__name__)
CONF = cfg.CONF


class DeviceOpenDaylight(abstract_driver.DeviceAbstractDriver):

    """OpenDaylight driver of hosting device."""

    def __init__(self):
        super(DeviceOpenDaylight, self).__init__()
        if 'opendaylight' in cfg.CONF.servicevm.infra_driver:
            self.odl_ip = cfg.CONF.opendaylight.ip
            self.odl_port = cfg.Conf.opendaylight.port
            self.username = cfg.Conf.opendaylight.username
            self.password = cfg.Conf.opendaylight.password
        else:
            LOG.warn(_('Unable to find opendaylight config in conf file'
                       'but opendaylight driver is loaded...'))

    def get_type(self):
        return 'opendaylight'

    def get_name(self):
        return 'opendaylight'

    def get_description(self):
        return 'OpenDaylight infra driver'

    def send_rest(self, data, rest_type, url):
        full_url = 'http://' + self.odl_ip + ':' + self.odl_port + '/' + url
        rest_call = getattr(requests, rest_type)
        if data is None:
            r = rest_call(full_url)
        else:
            r = rest_call(full_url, data=json.dumps(data), headers={'content-type': 'application/json'},
                          stream=False, auth=(self.username, self.password))

        if r.status_code != 200:
            return
        else:
            return r.json

    @log.log
    def list_network_topology(self):
        url = 'restconf/operational/network-topology:network-topology/'
        network = self.send_rest(None, 'get', url)
        return network

    @log.log
    def create_sff(self, sff_json):
        url = '/restconf/config/service-function-forwarder:service-function-forwarders/'
        sff_result = self.send_rest(sff_json, 'put', url)
        return sff_result

    @log.log
    def create_sfs(self, sfs_json):
        url = '/restconf/config/service-function:service-functions/'
        sfs_result = self.send_rest(sfs_json, 'put', url)
        return sfs_result

    @log.log
    def create_sfc(self, sfc_json):
        url = '/restconf/config/service-function-chain:service-function-chains/'
        raise NotImplementedError()

    @log.log
    def create_sfp(self, sfp_json):
        url = '/restconf/config/service-function-path:service-function-paths/'
        raise NotImplementedError()

    @log.log
    def create_rsp(self, rsp_json):
        url = '/restconf/operational/rendered-service-path:rendered-service-paths/'
        raise NotImplementedError()


