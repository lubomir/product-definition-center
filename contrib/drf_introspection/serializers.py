#
# Copyright (c) 2015 Red Hat
# Licensed under The MIT License (MIT)
# http://opensource.org/licenses/MIT
#
"""
This module defines three mixins intended for use with serializers.
"""

from rest_framework import serializers
from django.core.exceptions import FieldError


def _error_with_fields(message, fields):
    """
    Helper function to create an error message with a list of quoted, comma
    separated field names. The `message` argument should be a format string
    with a single `%s` placeholder.
    """
    return message % ', '.join('"%s"' % f for f in fields)


def _verify_field_names(fields, valid_fields, filter_name):
    if fields:
        invalid_field_names = set(fields) - valid_fields
        if invalid_field_names:
            error = 'Unknown fields in "%s" filter: %s' % (filter_name, ', '.join(invalid_field_names))
            raise FieldError(error)


def _pop_field_list(args, argument_name):
    values = args.pop(argument_name, [])
    if isinstance(values, list):
        return values
    return values.split(',')


class IntrospectableSerializerMixin(object):
    """
    Basic mixin for a serializer that supports introspection.

    The list of fields supported by the serializer is taken from the Meta
    class. If the serializer supports processing some input data that does not
    directly correspond to declared fields, it is possible to define a class
    attribute ``extra_fields``. These fields will also be included in the list.

    If the serializer is used in multiple contexts, it may be impossible to
    specify the extra fields in the class itself. In such case, it possible to
    pass an ``extra_fields`` argument to when creating the serializer. The
    value of this argument should be a list of strings.

    Both these options can be used simultaneously.
    """
    def __init__(self, *args, **kwargs):
        self._extra_fields = set()
        if hasattr(self.__class__, 'extra_fields'):
            self._extra_fields.update(self.__class__.extra_fields)
        self._extra_fields.update(kwargs.pop('extra_fields', []))
        super(IntrospectableSerializerMixin, self).__init__(*args, **kwargs)

    def get_allowed_keys(self):
        """
        Get a set of allowed field names for given serializer.
        """
        return set(self.fields.keys()) | self._extra_fields


class DynamicFieldsSerializerMixin(object):
    """
    A Serializer mixin that takes additional `fields` or `exclude_fields`
    list arguments that controls which fields should be displayed or not.

    NOTE: When given both, `exclude_fields` will be processed after `fields`.
    """

    query_params = ('fields', 'exclude_fields')

    def __init__(self, *args, **kwargs):
        # Accept kwargs in __init__, like:
        # Don't pass the 'fields' arg up to the superclass
        fields = _pop_field_list(kwargs, 'fields')
        exclude_fields = _pop_field_list(kwargs, 'exclude_fields')

        # Instantiate the superclass normally
        super(DynamicFieldsSerializerMixin, self).__init__(*args, **kwargs)

        # Accept PARAMS passing from the 'request' in the serializer's context.
        if hasattr(self, 'context') and isinstance(self.context, dict):
            request = self.context.get('request', None)
            # The 'fields' and 'exclude_fields" in request should only apply to
            # top level serializer.
            top_level = self.context.get('top_level', True)
            if request and top_level:
                fields += request.query_params.getlist('fields', [])
                exclude_fields += request.query_params.getlist('exclude_fields', [])

        valid_fields = set(self.fields.keys())
        _verify_field_names(fields, valid_fields, 'fields')
        _verify_field_names(exclude_fields, valid_fields, 'exclude_fields')

        # ignore nonexistent fields input
        if fields:
            fields = set(fields)
            # exclude_fields *rules* fields
            if exclude_fields:
                # Drop any fields that are specified in the `exclude_fields` argument.
                fields = fields - set(exclude_fields)
            # Drop any fields that are not specified in the `fields` argument.
            for field_name in valid_fields - fields:
                self.fields.pop(field_name)
        elif exclude_fields:
            # Drop any fields that are specified in the `exclude_fields` argument.
            for field_name in set(exclude_fields):
                self.fields.pop(field_name, None)


class StrictSerializerMixin(DynamicFieldsSerializerMixin, IntrospectableSerializerMixin):
    """
    A version of model serializer that raises a ``django.core.exceptions.FieldError``
    on deserializing data that includes unknown keys. This mixin inherits from
    the :class:`IntrospectableSerializerMixin` to be able to determine which
    fields are allowed.

    Additionally, if the input to the serializer is not a dict, a
    ``rest_framework.serializers.ValidationError`` will be raised. Also, when a
    read-only field is specified, a FieldError will be raised as well.

    This mixin also inherits from the :class:`DynamicFieldsSerializerMixin` to
    be able to select which fields to display.
    """
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError('Invalid input: must be a dict.')
        extra_fields = set(data.keys()) - self.get_allowed_keys()
        self.maybe_raise_error(extra_fields)
        self.check_read_only_fields(data.keys())
        return super(StrictSerializerMixin, self).to_internal_value(data)

    @staticmethod
    def maybe_raise_error(extra_fields):
        """
        Raise a ``django.core.exceptions.FieldError`` with a description
        including a list of given fields. If called with an empty set as
        argument, this function does not anything.

        This is not an instance method so that you can use it to generate error
        messages in other places, such as a view that does not use a
        serializer, but still wants to validate its input.
        """
        if extra_fields:
            raise FieldError(_error_with_fields('Unknown fields: %s.', extra_fields))

    def check_read_only_fields(self, keys):
        """
        Check that all fields are not read-only. If some are, a FieldError is
        raised with an error message.
        """
        updated_read_only = [key for key in keys if self._is_read_only(key)]
        if updated_read_only:
            raise FieldError(_error_with_fields('Can not update read only fields: %s.',
                                                updated_read_only))

    def _is_read_only(self, field_name):
        if field_name not in self.fields:
            return False
        return getattr(self.fields[field_name], 'read_only', False)
