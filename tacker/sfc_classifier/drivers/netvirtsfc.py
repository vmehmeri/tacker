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
from tacker.common import exceptions

LOG = logging.getLogger(__name__)
CONF = cfg.CONF

OPTS = [
    cfg.StrOpt('ip',
               default='127.0.0.1',
               help=_("OpenDaylight Controller address")),
    cfg.IntOpt('port',
               default=8080,
               help=_("OpenDaylight REST Port")),
    cfg.StrOpt('username',
               default='admin',
               help=_("OpenDaylight username")),
    cfg.StrOpt('password',
               default='admin',
               help=_("OpenDaylight password")),
]
CONF.register_opts(OPTS, group='sfc_opendaylight')


class NetVirtClassifierCreateFailed(exceptions.TackerException):
    message = _('NetVirt ODL Classifier could not be created')


class NetVirtSFC():

    """OpenDaylight netvirt driver for SFC Classification."""

    def __init__(self):
        if 'opendaylight' in cfg.CONF.sfc.infra_driver:
            self.odl_ip = cfg.CONF.sfc_opendaylight.ip
            self.odl_port = cfg.CONF.sfc_opendaylight.port
            self.username = cfg.CONF.sfc_opendaylight.username
            self.password = cfg.CONF.sfc_opendaylight.password
        else:
            LOG.warn(_('Unable to find opendaylight config in conf file'
                       'but netvirtsfc driver is loaded...'))
        self.sff_counter = 1
        self.config_acl_url = '/restconf/config/ietf-access-control-list:access-lists/{}/'

    def get_type(self):
        return 'netvirtsfc'

    def get_name(self):
        return 'netvirtsfc'

    def get_description(self):
        return 'OpenDaylight NetVirtSFC infra driver'

    @log.log
    def send_rest(self, data, rest_type, url):
        full_url = 'http://' + self.odl_ip + ':' + str(self.odl_port) + '/' + url
        rest_call = getattr(requests, rest_type)
        if data is None:
            r = rest_call(full_url, stream=False, auth=(self.username, self.password))
        else:
            r = rest_call(full_url, data=json.dumps(data), headers={'content-type': 'application/json'},
                          stream=False, auth=(self.username, self.password))
        LOG.debug(_('rest call response: %s'), r)
        return r

    @log.log
    def create_sfc_classifier(self, sfcc_dict, rsp_id):
        """
        :param sfcc_dict: dictionary that includes match criteria in
                          classifier request
        :param rsp_id: rendered service path instance ID
        :return: sfcc_id: classifier resource ID
        """
        sfcc_json =_build_classifier_json(sfcc_dict, rsp_id)
        sfcc_name = sfcc_dict['name']
        sfcc_result = self.send_rest(sfcc_json, 'put', self.config_acl_url.format(sfcc_name))

        if sfcc_result.status_code != 200:
            LOG.exception(_('Unable to create NetVirt Classifier'))
            raise NetVirtClassifierCreateFailed

        # FIXME right now there is no check in netvirtsfc to ensure classifier was created with id
        return sfcc_name

    @log.log
    def update_sfc_classifier(self, sfs_json):
        raise NotImplementedError

    @log.log
    def delete_sfc_classifier(self, classifier_id):
        sfcc_result = self.send_rest(None, 'delete', self.config_acl_url.format(classifier_id))
        return sfcc_result