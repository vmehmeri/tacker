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


import uuid
import json
import ast
import sqlalchemy as sa
from sqlalchemy import orm
from sqlalchemy.orm import exc as orm_exc

from tacker.api.v1 import attributes
from tacker import context as t_context
from tacker.db import api as qdbapi
from tacker.db import db_base
from tacker.db import model_base
from tacker.db import models_v1
from tacker.extensions import sfc
from tacker import manager
from tacker.openstack.common import log as logging
from tacker.openstack.common import uuidutils
from tacker.plugins.common import constants
from sqlalchemy.types import PickleType

LOG = logging.getLogger(__name__)
_ACTIVE_UPDATE = (constants.ACTIVE, constants.PENDING_UPDATE)
_ACTIVE_UPDATE_ERROR_DEAD = (
    constants.PENDING_CREATE, constants.ACTIVE, constants.PENDING_UPDATE,
    constants.ERROR, constants.DEAD)


###########################################################################
# db tables

# trozet TODO db implementation for SFC
class SFC(model_base.BASE, models_v1.HasTenant):
    """SFC Data Model
    """
    id = sa.Column(sa.String(255),
                   primary_key=True,
                   default=uuidutils.generate_uuid)

    name = sa.Column(sa.String(255), nullable=True)
    description = sa.Column(sa.String(255), nullable=True)

    instance_id = sa.Column(sa.String(255), nullable=True)

    attributes = orm.relationship("SFCAttribute", backref="sfc")

    status = sa.Column(sa.String(255), nullable=False)

    # driver to create sfc. e.g. opendaylight
    infra_driver = sa.Column(sa.String(255))

    # symmetry of chain
    symmetrical = sa.Column(sa.Boolean(), default=False)

    # chain
    chain = sa.Column(PickleType(pickler=json))

    # TODO associated classifiers

    # TODO vnfs in chain, as attrs?
    # vnf_chain = sa.Column(sa.String(255))


class SFCAttribute(model_base.BASE, models_v1.HasId):
    """Represents kwargs necessary for spinning up VM in (key, value) pair
    key value pair is adopted for being agnostic to actuall manager of VMs
    like nova, heat or others. e.g. image-id, flavor-id for Nova.
    The interpretation is up to actual driver of hosting device.
    """
    sfc_id = sa.Column(sa.String(255), sa.ForeignKey('sfcs.id'),
                       nullable=False)
    key = sa.Column(sa.String(255), nullable=False)
    # json encoded value. example
    # "nic": [{"net-id": <net-uuid>}, {"port-id": <port-uuid>}]
    value = sa.Column(sa.String(4096), nullable=True)


class SFCPluginDb(sfc.SFCPluginBase, db_base.CommonDbMixin):

    @property
    def _core_plugin(self):
        return manager.TackerManager.get_plugin()

    def __init__(self):
        qdbapi.register_models()
        super(SFCPluginDb, self).__init__()

    def _get_resource(self, context, model, id):
        try:
            return self._get_by_id(context, model, id)
        except orm_exc.NoResultFound:
            if issubclass(model, SFC):
                raise sfc.SFCNotFound(sfc_id=id)
            else:
                raise BaseException

    def _make_attributes_dict(self, attributes_db):
        return dict((attr.key, attr.value) for attr in attributes_db)

    @staticmethod
    def _infra_driver_name(sfc_dict):
        return sfc_dict['infra_driver']

    @staticmethod
    def _instance_id(sfc_dict):
        return sfc_dict['instance_id']

    # called internally, not by REST API
    def _create_sfc_pre(self, context, sfc):
        sfc = sfc['sfc']
        LOG.debug(_('sfc %s'), sfc)
        tenant_id = self._get_tenant_id_for_create(context, sfc)
        infra_driver = sfc.get('infra_driver')
        name = sfc.get('name')
        description = sfc.get('description')
        sfc_id = sfc.get('id') or str(uuid.uuid4())
        attributes = sfc.get('attributes', {})
        symmetrical = sfc.get('symmetrical', 'False')
        if type(symmetrical) is not bool:
            symmetrical = ast.literal_eval(symmetrical)
        chain = sfc.get('chain')

        with context.session.begin(subtransactions=True):

            sfc_db = SFC(id=sfc_id,
                         tenant_id=tenant_id,
                         name=name,
                         description=description,
                         instance_id=None,
                         infra_driver=infra_driver,
                         symmetrical=symmetrical,
                         chain=chain,
                         status=constants.PENDING_CREATE)
            context.session.add(sfc_db)
            for key, value in attributes.items():
                arg = SFCAttribute(
                    id=str(uuid.uuid4()), sfc_id=sfc_id,
                    key=key, value=value)
                context.session.add(arg)

        return self._make_sfc_dict(sfc_db)

    # reference implementation. needs to be overridden by subclass
    def create_sfc(self, context, sfc):
        sfc_dict = self._create_device_pre(context, sfc)
        # start actual creation of hosting device.
        # Waiting for completion of creation should be done in background
        # by another thread if it takes a while.
        instance_id = str(uuid.uuid4())
        sfc_dict['instance_id'] = instance_id
        self._create_sfc_post(context, sfc_dict['id'], instance_id, sfc_dict)
        self._create_sfc_status(context, sfc_dict['id'],
                                constants.ACTIVE)
        return sfc_dict

    # called internally, not by REST API
    # instance_id = None means error on creation
    def _create_sfc_post(self, context, sfc_id, instance_id,
                         sfc_dict):
        LOG.debug(_('sfc_dict %s'), sfc_dict)
        with context.session.begin(subtransactions=True):
            query = (self._model_query(context, SFC).
                     filter(SFC.id == sfc_id).
                     filter(SFC.status == constants.PENDING_CREATE).
                     one())
            query.update({'instance_id': instance_id})
            if instance_id is None:
                query.update({'status': constants.ERROR})

            for (key, value) in sfc_dict['attributes'].items():
                self._sfc_attribute_update_or_create(context, sfc_id,
                                                     key, value)

    def _sfc_attribute_update_or_create(
            self, context, sfc_id, key, value):
        arg = (self._model_query(context, SFCAttribute).
               filter(SFCAttribute.sfc_id == sfc_id).
               filter(SFCAttribute.key == key).first())
        if arg:
            arg.value = value
        else:
            arg = SFCAttribute(
                id=str(uuid.uuid4()), sfc_id=sfc_id,
                key=key, value=value)
            context.session.add(arg)

    def _create_sfc_status(self, context, sfc_id, new_status):
        with context.session.begin(subtransactions=True):
            (self._model_query(context, SFC).
                filter(SFC.id == sfc_id).
                filter(SFC.status == constants.PENDING_CREATE).
                update({'status': new_status}))

    def _make_sfc_attrs_dict(self, sfc_attrs_db):
        return dict((arg.key, arg.value) for arg in sfc_attrs_db)

    def _make_sfc_dict(self, sfc_db, fields=None):
        LOG.debug(_('sfc_db %s'), sfc_db)
        LOG.debug(_('sfc_db attributes %s'), sfc_db.attributes)
        res = {
            'attributes': self._make_sfc_attrs_dict(sfc_db.attributes)
        }
        key_list = ('id', 'tenant_id', 'name', 'description', 'instance_id',
                    'infra_driver', 'status', 'symmetrical', 'chain')
        res.update((key, sfc_db[key]) for key in key_list)
        return self._fields(res, fields)

    def _get_sfc_db(self, context, sfc_id, current_statuses, new_status):
        try:
            sfc_db = (
                self._model_query(context, SFC).
                filter(SFC.id == sfc_id).
                filter(SFC.status.in_(current_statuses)).
                with_lockmode('update').one())
        except orm_exc.NoResultFound:
            raise sfc.SFCNotFound(sfc_id=sfc_id)
        if sfc_db.status == constants.PENDING_UPDATE:
            raise sfc.SFCInUse(sfc_id=sfc_id)
        sfc_db.update({'status': new_status})
        return sfc_db

    def get_sfc(self, context, sfc_id, fields=None):
        sfc_db = self._get_resource(context, SFC, sfc_id)
        return self._make_sfc_dict(sfc_db, fields)

    def get_sfcs(self, context, filters=None, fields=None):
        sfcs = self._get_collection(context, SFC, self._make_sfc_dict,
                                    filters=filters, fields=fields)
        # Ugly hack to mask internally used record
        return [sfc for sfc in sfcs
                if uuidutils.is_uuid_like(sfc['id'])]

    # reference implementation. needs to be overridden by subclass
    def update_sfc(self, context, sfc_id, sfc):
        sfc_dict = self._update_sfc_pre(context, sfc_id)
        # start actual update of hosting device
        # waiting for completion of update should be done backgroundly
        # by another thread if it takes a while
        self._update_sfc_post(context, sfc_id, constants.ACTIVE)
        return sfc_dict

    def _update_sfc_pre(self, context, device_id):
        with context.session.begin(subtransactions=True):
            sfc_db = self._get_sfc_db(
                context, device_id, _ACTIVE_UPDATE, constants.PENDING_UPDATE)
        return self._make_sfc_dict(sfc_db)

    def _update_sfc_post(self, context, sfc_id, new_status,
                         new_sfc_dict=None):
        with context.session.begin(subtransactions=True):
            (self._model_query(context, SFC).
             filter(SFC.id == sfc_id).
             filter(SFC.status == constants.PENDING_UPDATE).
             update({'status': new_status}))

            sfc_attrs = new_sfc_dict.get('attributes', {})
            (context.session.query(SFCAttribute).
             filter(SFCAttribute.device_id == sfc_id).
             filter(~SFCAttribute.key.in_(sfc_attrs.keys())).
             delete(synchronize_session='fetch'))

            for (key, value) in sfc_attrs.items():
                self._sfc_attribute_update_or_create(context, sfc_id,
                                                     key, value)

    def _delete_sfc_pre(self, context, sfc_id):
        with context.session.begin(subtransactions=True):
            sfc_db = self._get_sfc_db(
                context, sfc_id, _ACTIVE_UPDATE_ERROR_DEAD,
                constants.PENDING_DELETE)

        return self._make_sfc_dict(sfc_db)

    def _delete_sfc_post(self, context, sfc_id, error):
        with context.session.begin(subtransactions=True):
            query = (
                self._model_query(context, SFC).
                filter(SFC.id == sfc_id).
                filter(SFC.status == constants.PENDING_DELETE))
            if error:
                query.update({'status': constants.ERROR})
            else:
                (self._model_query(context, SFCAttribute).
                 filter(SFCAttribute.sfc_id == sfc_id).delete())
                query.delete()

    # reference implementation. needs to be overridden by subclass
    def delete_sfc(self, context, sfc_id):
        self._delete_sfc_pre(context, sfc_id)
        # start actual deletion of hosting device.
        # Waiting for completion of deletion should be done in background
        # by another thread if it takes a while.
        self._delete_sfc_post(context, sfc_id, False)
