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


import abc

import six

from tacker.api import extensions
from tacker.api.v1 import attributes as attr
from tacker.api.v1 import resource_helper
from tacker.common import exceptions
from tacker.openstack.common import log as logging
from tacker.plugins.common import constants
from tacker.services.service_base import NFVPluginBase


LOG = logging.getLogger(__name__)


class InfraDriverNotSpecified(exceptions.InvalidInput):
    message = _('infra driver is not specified')


class SFCCreateWaitFailed(exceptions.TackerException):
    message = _('waiting for creation of sfc %(sfc_id)s failed')


class SFCNotFound(exceptions.NotFound):
    message = _('SFC %(sfc_id)s could not be found')


class SFCInUse(exceptions.InUse):
    message = _('SFC %(sfc_id)s is still in use')


# trozet TODO add correct attribute validators and exceptions
class SFCNotFound(exceptions.NotFound):
    message = _('sfc %(sfc_id)s could not be found')

RESOURCE_ATTRIBUTE_MAP = {

    'sfcs': {
        'id': {
            'allow_post': False,
            'allow_put': False,
            'validate': {'type:uuid': None},
            'is_visible': True,
            'primary_key': True
        },
        'tenant_id': {
            'allow_post': True,
            'allow_put': False,
            'validate': {'type:string': None},
            'required_by_policy': True,
            'is_visible': True
        },
        'name': {
            'allow_post': True,
            'allow_put': True,
            'validate': {'type:string': None},
            'is_visible': True,
            'default': '',
        },
        'description': {
            'allow_post': True,
            'allow_put': True,
            'validate': {'type:string': None},
            'is_visible': True,
            'default': '',
        },
        'infra_driver': {
            'allow_post': True,
            'allow_put': False,
            'validate': {'type:string': None},
            'is_visible': True,
            'default': attr.ATTR_NOT_SPECIFIED,
        },
        'instance_id': {
            'allow_post': False,
            'allow_put': False,
            'validate': {'type:string': None},
            'is_visible': True,
        },
        'mgmt_url': {
            'allow_post': False,
            'allow_put': False,
            'validate': {'type:string': None},
            'is_visible': True,
        },
        'attributes': {
            'allow_post': True,
            'allow_put': True,
            'validate': {'type:dict_or_none': None},
            'is_visible': True,
            'default': {},
        },
        'service_contexts': {
            'allow_post': True,
            'allow_put': False,
            'validate': {'type:service_context_list': None},
            'is_visible': True,
            'default': [],
        },
        'services': {
            'allow_post': False,
            'allow_put': False,
            'validate': {'type:uuid': None},
            'is_visible': True,
        },
        'status': {
            'allow_post': False,
            'allow_put': False,
            'is_visible': True,
        },
    },
}


class Sfc(extensions.ExtensionDescriptor):
    @classmethod
    def get_name(cls):
        return 'SFC'

    @classmethod
    def get_alias(cls):
        return 'SFC Manager'

    @classmethod
    def get_description(cls):
        return "Extension for Service Function Chaining"

    @classmethod
    def get_namespace(cls):
        return 'http://wiki.openstack.org/Tacker'

    @classmethod
    def get_updated(cls):
        return "2015-10-08T10:00:00-00:00"

    @classmethod
    def get_resources(cls):
        special_mappings = {}
        plural_mappings = resource_helper.build_plural_mappings(
            special_mappings, RESOURCE_ATTRIBUTE_MAP)
        plural_mappings['sfcs'] = 'sfc'
        attr.PLURALS.update(plural_mappings)
        return resource_helper.build_resource_info(
            plural_mappings, RESOURCE_ATTRIBUTE_MAP, constants.SFC,
            translate_name=True)

    @classmethod
    def get_plugin_interface(cls):
        return SFCPluginBase

    def update_attributes_map(self, attributes):
        super(Sfc, self).update_attributes_map(
            attributes, extension_attrs_map=RESOURCE_ATTRIBUTE_MAP)

    def get_extended_resources(self, version):
        version_map = {'1.0': RESOURCE_ATTRIBUTE_MAP}
        return version_map.get(version, {})


@six.add_metaclass(abc.ABCMeta)
class SFCPluginBase(NFVPluginBase):
    def get_plugin_name(self):
        return constants.SFC

    def get_plugin_type(self):
        return constants.SFC

    def get_plugin_description(self):
        return 'Tacker SFC plugin'

    @abc.abstractmethod
    def get_sfcs(self, context, filters=None, fields=None):
        pass

    @abc.abstractmethod
    def get_sfc(self, context, sfc_id, fields=None):
        pass

    @abc.abstractmethod
    def create_sfc(self, context, chain):
        pass

    @abc.abstractmethod
    def update_sfc(self, context, sfc_id, chain):
        pass

    @abc.abstractmethod
    def delete_sfc(self, context, sfc_id):
        pass
