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
from tacker.db.sfc_classifier import sfc_classifier_db
from tacker.extensions import sfc_classifier
from tacker.openstack.common import excutils
from tacker.openstack.common import log as logging
from tacker.plugins.common import constants
from neutronclient.v2_0 import client as neutron_client
from tacker.db.vm.vm_db import VNFMPluginDb
from tacker import manager
import json
import re

LOG = logging.getLogger(__name__)


class SFCCPlugin(sfc_classifier_db.SFCCPluginDb):
    """SFCPlugin which supports SFC framework
    """
    OPTS = [
        cfg.ListOpt(
            'infra_driver', default=['netvirtsfc'],
            help=_('Hosting device drivers sfc plugin will use')),
    ]
    cfg.CONF.register_opts(OPTS, 'sfc_classifier')
    supported_extension_aliases = ['sfc_classifier']

    def __init__(self):
        super(SFCCPlugin, self).__init__()
        self._pool = eventlet.GreenPool()
        self._device_manager = driver_manager.DriverManager(
            'tacker.sfc_classifier.drivers',
            cfg.CONF.sfc_classifier.infra_driver)

    def spawn_n(self, function, *args, **kwargs):
        self._pool.spawn_n(function, *args, **kwargs)

    def _find_sfc_instance(self, context, chain_id):
        sfc_plugin = manager.TackerManager.get_service_plugins()['SFC']
        sfc_dict = sfc_plugin.get_sfc(context, chain_id)
        sfc_id = sfc_dict['instance_id']
        return sfc_id

    def _create_sfc_classifier(self, context, sfccd):
        """
        :param context:
        :param sfccd: dictionary of kwargs from REST request
        :return: dictionary of created object
        """
        sfcc_dict = sfccd['sfc_classifier']
        LOG.debug(_('classifier_dict %s'), sfcc_dict)
        # check to make sure SFC exists
        # and get SFC instance_id
        sfc_id = self._find_sfc_instance(context, sfcc_dict['chain'])
        LOG.debug(_('Matching SFC Instance: %s'), sfc_id)
        sfcc_dict = self._create_sfc_classifier_pre(context, sfccd)
        LOG.debug(_('sfcc_dict after database entry %s'), sfcc_dict)

        sfcc_id = sfcc_dict['id']
        # Default driver for SFC Classifier is netvirtsfc
        if 'infra_driver' not in sfcc_dict:
            infra_driver = 'netvirtsfc'
        else:
            infra_driver = sfcc_dict['infra_driver']

        # we want to present the real instance ID to the driver
        instance_id = self._device_manager.invoke(infra_driver, 'create_sfc_classifier', sfcc_dict=sfcc_dict,
                                                  chain_instance_id=sfc_id)

        if instance_id is None:
            self._create_sfc_classifier_post(context, sfcc_id, None, sfcc_dict)
            self._create_sfc_classifier_status(context, sfcc_id, constants.ERROR)
            return sfcc_dict

        sfcc_dict['instance_id'] = instance_id
        LOG.debug(_('sfcc_dict after sfc SFC Classifier Create complete: %s'), sfcc_dict)
        self._create_sfc_classifier_post(context, sfcc_id, instance_id, sfcc_dict)
        new_status = constants.ACTIVE
        self._create_sfc_classifier_status(context, sfcc_id, new_status)

        return sfcc_dict

    def create_sfc_classifier(self, context, sfc_classifier):
        self.sfc_classifier_exists(context, sfc_classifier['sfc_classifier']['name'])
        sfcc_dict = self._create_sfc_classifier(context, sfc_classifier)
        # TODO fix this or remove it, not sure if ODL is synchronous here
        #def create_sfc_wait():
        #    self._create_sfc_wait(context, sfc_dict)

        #self.spawn_n(create_sfc_wait)
        return sfcc_dict

    def _create_sfc_classifier_wait(self, context, sfcc_dict):
        driver_name = self._infra_driver_name(sfcc_dict)
        sfcc_id = sfcc_dict['id']
        instance_id = self._instance_id(sfcc_dict)

        try:
            self._device_manager.invoke(
                driver_name, 'create_wait',
                sfcc_dict=sfcc_dict, sfcc_id=instance_id)
        except sfc_classifier.ClassifierCreateWaitFailed:
            instance_id = None
            del sfcc_dict['instance_id']

        self._create_sfc_classifier_post(
            context, sfcc_id, instance_id, sfcc_dict)
        if instance_id is None:
            return

        new_status = constants.ACTIVE

        sfcc_dict['status'] = new_status
        self._create_sfc_status(context, sfcc_id, new_status)

    def delete_sfc_classifier(self, context, sfcc_id):
        sfcc_dict = self._delete_sfc_classifier_pre(context, sfcc_id)
        driver_name = self._infra_driver_name(sfcc_dict)
        instance_id = self._instance_id(sfcc_dict)

        try:
            self._device_manager.invoke(driver_name, 'delete_sfc_classifier',
                                        instance_id=instance_id)
        except Exception as e:
            with excutils.save_and_reraise_exception():
                self._delete_sfc_classifier_post(context, sfcc_id, e)

        self._delete_sfc_classifier_post(context, sfcc_id, None)

    def update_sfc(self, context, sfcc_id, sfcc):
        sfcc_dict = self._update_sfc_classifier_pre(context, sfcc_id)
        driver_name = self._infra_driver_name(sfcc_dict)
        instance_id = self._instance_id(sfcc_dict)
        update_dict = sfcc['sfc_classifier']
        try:
            self._device_manager.invoke(
                driver_name, 'update_sfc_classifier', plugin=self, update_sfcc_dict=update_dict,
                instance_id=instance_id)
        except Exception:
            with excutils.save_and_reraise_exception():
                self._update_sfc_classifier_post(context, sfcc_id, constants.ERROR)

        # TODO implement update wait and check updated
        new_status = constants.ACTIVE
        self._update_sfc_classifier_post(context, sfcc_dict['id'],
                                         new_status, update_dict)
        return update_dict
