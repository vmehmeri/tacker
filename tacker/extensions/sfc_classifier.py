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


class ClassifierNotFound(exceptions.NotFound):
    message = _('Classifier %(sfcc_id)s could not be found')


class ClassifierInUse(exceptions.InUse):
    message = _('SFC Classifier%(sfcc_id)s is still in use')


class SFCNotFound(exceptions.NotFound):
    message = _('SFC %(sfc_id)s not found to attach to')


class SFCCreateFailed(exceptions.TackerException):
    message = _('SFC %(sfc_id)s could not be created')


class ClassifierAlreadyExists(exceptions.InUse):
    message = _('SFC Classifier with name %(sfcc_name)s already exists!')


class ClassifierCreateWaitFailed(exceptions.TackerException):
    message = _('waiting for creation of sfc classifier %(sfcc_id)s failed')


def _validate_acl_match_criteria(data, valid_values=None):
    if not isinstance(data, dict):
        msg = _("invalid data format for acl match dict: '%s'") % data
        LOG.debug(msg)
        return msg

    key_specs = {
        'source_mac': {'type:mac_address': None},
        'dest_mac': {'type:mac_address': None},
        'ethertype': {'type:string': None},
        'source_ip_prefix': {'type:ip_network': None},
        'dest_ip_prefix': {'type:ip_network': None},
        'source_port': {'type:port': None,
                        'convert_to': attr.convert_to_int},
        'dest_port': {'type:port': None,
                      'convert_to': attr.convert_to_int},
        'protocol': {'type:non_negative': None,
                     'convert_to': attr.convert_to_int}
    }

    msg = attr._validate_dict_or_empty(data, key_specs=key_specs)
    if msg:
        LOG.debug(msg)
        return msg

attr.validators['type:acl_dict'] = _validate_acl_match_criteria

RESOURCE_ATTRIBUTE_MAP = {

    'sfc_classifiers': {
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
        'chain': {
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
            'default': 'netvirtsfc',
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
        'match': {
            'allow_post': True,
            'allow_put': True,
            'validate': {'type:acl_dict': None},
            'is_visible': True,
            'default': {},
        },
        'acl_match_criteria': {
            'allow_post': True,
            'allow_put': True,
            'validate': {'type:acl_dict': None},
            'is_visible': True,
            'default': {},
        }
    },
}


class Sfc_classifier(extensions.ExtensionDescriptor):
    @classmethod
    def get_name(cls):
        return 'SFCClassifier'

    @classmethod
    def get_alias(cls):
        return 'SFC Classifier'

    @classmethod
    def get_description(cls):
        return "Extension for Service Function Chaining Classifier"

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
        plural_mappings['sfc_classifiers'] = 'sfc_classifier'
        attr.PLURALS.update(plural_mappings)
        return resource_helper.build_resource_info(
            plural_mappings, RESOURCE_ATTRIBUTE_MAP, constants.SFC_CLASSIFIER,
            translate_name=True)

    @classmethod
    def get_plugin_interface(cls):
        return SFCCPluginBase

    def update_attributes_map(self, attributes):
        super(Sfc_classifier, self).update_attributes_map(
            attributes, extension_attrs_map=RESOURCE_ATTRIBUTE_MAP)

    def get_extended_resources(self, version):
        version_map = {'1.0': RESOURCE_ATTRIBUTE_MAP}
        return version_map.get(version, {})


@six.add_metaclass(abc.ABCMeta)
class SFCCPluginBase(NFVPluginBase):
    def get_plugin_name(self):
        return constants.SFC_CLASSIFIER

    def get_plugin_type(self):
        return constants.SFC_CLASSIFIER

    def get_plugin_description(self):
        return 'Tacker SFC Classifier plugin'

    @abc.abstractmethod
    def get_sfc_classifiers(self, context, filters=None, fields=None):
        pass

    @abc.abstractmethod
    def get_sfc_classifier(self, context, sfcc_id, fields=None):
        pass

    @abc.abstractmethod
    def create_sfc_classifier(self, context, classifier):
        pass

    @abc.abstractmethod
    def update_sfc_classifier(self, context, sfcc_id, classifier):
        pass

    @abc.abstractmethod
    def delete_sfc_classifier(self, context, sfcc_id):
        pass
