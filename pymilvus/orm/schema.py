# Copyright (C) 2019-2021 Zilliz. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except
# in compliance with the License. You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distributed under the License
# is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
# or implied. See the License for the specific language governing permissions and limitations under
# the License.

import copy
from typing import Any, Dict, List, Optional, Union

import pandas as pd
from pandas.api.types import is_list_like, is_scalar

from pymilvus.exceptions import (
    AutoIDException,
    CannotInferSchemaException,
    DataNotMatchException,
    DataTypeNotSupportException,
    ExceptionsMessage,
    FieldsTypeException,
    FieldTypeException,
    ParamError,
    PartitionKeyException,
    PrimaryKeyException,
    SchemaNotReadyException,
    UpsertAutoIDTrueException,
)
from pymilvus.grpc_gen import schema_pb2 as schema_types

from .constants import COMMON_TYPE_PARAMS
from .types import (
    DataType,
    infer_dtype_by_scaladata,
    infer_dtype_bydata,
    map_numpy_dtype_to_datatype,
)


def validate_primary_key(primary_field: Any):
    if primary_field is None:
        raise PrimaryKeyException(message=ExceptionsMessage.PrimaryKeyNotExist)

    if primary_field.dtype not in [DataType.INT64, DataType.VARCHAR]:
        raise PrimaryKeyException(message=ExceptionsMessage.PrimaryKeyType)


def validate_partition_key(
    partition_key_field_name: Any, partition_key_field: Any, primary_field_name: Any
):
    # not allow partition_key field is primary key field
    if partition_key_field is not None:
        if partition_key_field.name == primary_field_name:
            PartitionKeyException(message=ExceptionsMessage.PartitionKeyNotPrimary)

        if partition_key_field.dtype not in [DataType.INT64, DataType.VARCHAR]:
            raise PartitionKeyException(message=ExceptionsMessage.PartitionKeyType)
    elif partition_key_field_name is not None:
        raise PartitionKeyException(
            message=ExceptionsMessage.PartitionKeyFieldNotExist % partition_key_field_name
        )


class CollectionSchema:
    def __init__(self, fields: List, description: str = "", **kwargs):
        self._kwargs = copy.deepcopy(kwargs)
        self._fields = []
        self._description = description
        self._enable_dynamic_field = self._kwargs.get("enable_dynamic_field", False)
        self._primary_field = None
        self._partition_key_field = None

        if not isinstance(fields, list):
            raise FieldsTypeException(message=ExceptionsMessage.FieldsType)
        self._fields = [copy.deepcopy(field) for field in fields]

        self._check_kwargs()
        if kwargs.get("check_fields", True):
            self._check_fields()

    def _check_kwargs(self):
        primary_field_name = self._kwargs.get("primary_field", None)
        partition_key_field_name = self._kwargs.get("partition_key_field", None)
        if primary_field_name is not None and not isinstance(primary_field_name, str):
            raise PrimaryKeyException(message=ExceptionsMessage.PrimaryFieldType)
        if partition_key_field_name is not None and not isinstance(partition_key_field_name, str):
            raise PartitionKeyException(message=ExceptionsMessage.PartitionKeyFieldType)

        for field in self._fields:
            if not isinstance(field, FieldSchema):
                raise FieldTypeException(message=ExceptionsMessage.FieldType)

        if "auto_id" in self._kwargs and not isinstance(self._kwargs["auto_id"], bool):
            raise AutoIDException(0, ExceptionsMessage.AutoIDType)

    def _check_fields(self):
        primary_field_name = self._kwargs.get("primary_field", None)
        partition_key_field_name = self._kwargs.get("partition_key_field", None)
        for field in self._fields:
            if primary_field_name == field.name:
                field.is_primary = True
            if partition_key_field_name == field.name:
                field.is_partition_key = True

            if field.is_primary:
                if primary_field_name is not None and primary_field_name != field.name:
                    msg = ExceptionsMessage.PrimaryKeyOnlyOne % (primary_field_name, field.name)
                    raise PrimaryKeyException(message=msg)
                self._primary_field = field
                primary_field_name = field.name

            if field.is_partition_key:
                if partition_key_field_name is not None and partition_key_field_name != field.name:
                    msg = ExceptionsMessage.PartitionKeyOnlyOne % (
                        partition_key_field_name,
                        field.name,
                    )
                    raise PartitionKeyException(message=msg)
                self._partition_key_field = field
                partition_key_field_name = field.name

        validate_primary_key(self._primary_field)
        validate_partition_key(
            partition_key_field_name, self._partition_key_field, self._primary_field.name
        )

        auto_id = self._kwargs.get("auto_id", False)
        if auto_id:
            self._primary_field.auto_id = auto_id

    def _check(self):
        self._check_kwargs()
        self._check_fields()

    def __repr__(self) -> str:
        return str(self.to_dict())

    def __len__(self) -> int:
        return len(self.fields)

    def __eq__(self, other: Any):
        """The order of the fields of schema must be consistent."""
        return self.to_dict() == other.to_dict()

    @classmethod
    def construct_from_dict(cls, raw: Dict):
        fields = [FieldSchema.construct_from_dict(field_raw) for field_raw in raw["fields"]]
        enable_dynamic_field = raw.get("enable_dynamic_field", False)
        return CollectionSchema(
            fields, raw.get("description", ""), enable_dynamic_field=enable_dynamic_field
        )

    @property
    def primary_field(self):
        return self._primary_field

    @property
    def partition_key_field(self):
        return self._partition_key_field

    @property
    def fields(self):
        """
        Returns the fields about the CollectionSchema.

        :return list:
            List of FieldSchema, return when operation is successful.

        :example:
            >>> from pymilvus import FieldSchema, CollectionSchema, DataType
            >>> field = FieldSchema("int64", DataType.INT64, description="int64", is_primary=True)
            >>> schema = CollectionSchema(fields=[field])
            >>> schema.fields
            [<pymilvus.schema.FieldSchema object at 0x7fd3716ffc50>]
        """
        return self._fields

    @property
    def description(self):
        """
        Returns a text description of the CollectionSchema.

        :return str:
            CollectionSchema description text, return when operation is successful.

        :example:
            >>> from pymilvus import FieldSchema, CollectionSchema, DataType
            >>> field = FieldSchema("int64", DataType.INT64, description="int64", is_primary=True)
            >>> schema = CollectionSchema(fields=[field], description="test get description")
            >>> schema.description
            'test get description'
        """
        return self._description

    @property
    def auto_id(self):
        """
        Whether the primary keys are automatically generated.

        :return bool:
            * True: If the primary keys are automatically generated,
            * False: Otherwise.

        :example:
            >>> from pymilvus import FieldSchema, CollectionSchema, DataType
            >>> field = FieldSchema("int64", DataType.INT64, description="int64", is_primary=True)
            >>> schema = CollectionSchema(fields=[field])
            >>> schema.auto_id
            False
        """
        return self.primary_field.auto_id

    @auto_id.setter
    def auto_id(self, value: bool):
        if self.primary_field:
            self.primary_field.auto_id = bool(value)

    @property
    def enable_dynamic_field(self):
        return self._enable_dynamic_field

    @enable_dynamic_field.setter
    def enable_dynamic_field(self, value: bool):
        self._enable_dynamic_field = bool(value)

    def to_dict(self):
        _dict = {
            "auto_id": self.auto_id,
            "description": self._description,
            "fields": [s.to_dict() for s in self._fields],
        }
        if self._enable_dynamic_field:
            _dict["enable_dynamic_field"] = self._enable_dynamic_field
        return _dict

    def verify(self):
        # final check, detect obvious problems
        self._check()

    def add_field(self, field_name: str, datatype: DataType, **kwargs):
        field = FieldSchema(field_name, datatype, **kwargs)
        self._fields.append(field)
        return self


class FieldSchema:
    def __init__(self, name: str, dtype: DataType, description: str = "", **kwargs) -> None:
        self.name = name
        try:
            dtype = DataType(dtype)
        except ValueError:
            raise DataTypeNotSupportException(message=ExceptionsMessage.FieldDtype) from None
        if dtype == DataType.UNKNOWN:
            raise DataTypeNotSupportException(message=ExceptionsMessage.FieldDtype)
        self._dtype = dtype
        self._description = description
        self._type_params = {}
        self._kwargs = copy.deepcopy(kwargs)
        if not isinstance(kwargs.get("is_primary", False), bool):
            raise PrimaryKeyException(message=ExceptionsMessage.IsPrimaryType)
        self.is_primary = kwargs.get("is_primary", False)
        self.is_dynamic = kwargs.get("is_dynamic", False)
        self.auto_id = kwargs.get("auto_id", False)
        if "auto_id" in kwargs:
            if not isinstance(self.auto_id, bool):
                raise AutoIDException(message=ExceptionsMessage.AutoIDType)
            if not self.is_primary and self.auto_id:
                raise PrimaryKeyException(message=ExceptionsMessage.AutoIDOnlyOnPK)

        if not isinstance(kwargs.get("is_partition_key", False), bool):
            raise PartitionKeyException(message=ExceptionsMessage.IsPartitionKeyType)
        self.is_partition_key = kwargs.get("is_partition_key", False)
        self.default_value = kwargs.get("default_value", None)
        if isinstance(self.default_value, schema_types.ValueField):
            if self.default_value.WhichOneof("data") is None:
                self.default_value = None
        else:
            self.default_value = infer_default_value_bydata(kwargs.get("default_value", None))
        self._parse_type_params()

    def __repr__(self) -> str:
        return str(self.to_dict())

    def __deepcopy__(self, memodict: Optional[Dict] = None):
        if memodict is None:
            memodict = {}
        return self.construct_from_dict(self.to_dict())

    def _parse_type_params(self):
        # update self._type_params according to self._kwargs
        if self._dtype not in (DataType.BINARY_VECTOR, DataType.FLOAT_VECTOR, DataType.VARCHAR):
            return
        if not self._kwargs:
            return
        # currently only support ndim
        if self._kwargs:
            for k in COMMON_TYPE_PARAMS:
                if k in self._kwargs:
                    if self._type_params is None:
                        self._type_params = {}
                    self._type_params[k] = self._kwargs[k]

    @classmethod
    def construct_from_dict(cls, raw: Dict):
        kwargs = {}
        kwargs.update(raw.get("params", {}))
        kwargs["is_primary"] = raw.get("is_primary", False)
        if raw.get("auto_id", None) is not None:
            kwargs["auto_id"] = raw.get("auto_id", None)
        kwargs["is_partition_key"] = raw.get("is_partition_key", False)
        kwargs["default_value"] = raw.get("default_value", None)
        kwargs["is_dynamic"] = raw.get("is_dynamic", False)
        return FieldSchema(raw["name"], raw["type"], raw.get("description", ""), **kwargs)

    def to_dict(self):
        _dict = {
            "name": self.name,
            "description": self._description,
            "type": self.dtype,
        }
        if self._type_params:
            _dict["params"] = copy.deepcopy(self.params)
        if self.is_primary:
            _dict["is_primary"] = True
            _dict["auto_id"] = self.auto_id
        if self.is_partition_key:
            _dict["is_partition_key"] = True
        if self.default_value is not None:
            if self.default_value.WhichOneof("data") is None:
                self.default_value = None
            _dict["default_value"] = self.default_value
        if self.is_dynamic:
            _dict["is_dynamic"] = self.is_dynamic
        return _dict

    def __getattr__(self, item: str):
        if self._type_params and item in self._type_params:
            return self._type_params[item]
        return None

    def __eq__(self, other: Any):
        if not isinstance(other, FieldSchema):
            return False
        return self.to_dict() == other.to_dict()

    @property
    def description(self):
        """
        Returns the text description of the FieldSchema.

        :return str:
            FieldSchema description text, returned when the operation is successful.

        :example:
        >>> from pymilvus import FieldSchema, DataType
        >>> field = FieldSchema("int64", DataType.INT64, description="int64", is_primary=False)
        >>> field.description
        'int64'
        """
        return self._description

    @property
    def params(self):
        """
        Returns the parameters of the field.

        :return list:
            List of the parameter.

        :example:
        >>> from pymilvus import FieldSchema, DataType
        >>> field = FieldSchema("int64", DataType.INT64, description="int64", is_primary=False)
        >>> field.params
        {}
        >>> fvec_field = FieldSchema("fvec", DataType.FLOAT_VECTOR, is_primary=False, dim=128)
        >>> fvec_field.params
        {'dim': 128}
        """
        return self._type_params

    @property
    def dtype(self) -> DataType:
        return self._dtype


def check_is_row_based(data: Union[List[List], List[Dict], Dict, pd.DataFrame]) -> bool:
    if not isinstance(data, (pd.DataFrame, list, dict)):
        raise DataTypeNotSupportException(
            message="The type of data should be list or pandas.DataFrame or dict"
        )

    if isinstance(data, pd.DataFrame):
        return False

    if isinstance(data, dict):
        return True

    if isinstance(data, list):
        if len(data) == 0:
            return False
        if isinstance(data[0], Dict):
            return True

    return False


def check_insert_schema(schema: CollectionSchema, data: Union[List[List], pd.DataFrame]):
    if schema is None:
        raise SchemaNotReadyException(message="Schema shouldn't be None")
    if schema.auto_id and isinstance(data, pd.DataFrame) and schema.primary_field.name in data:
        if not data[schema.primary_field.name].isnull().all():
            msg = f"Expect no data for auto_id primary field: {schema.primary_field.name}"
            raise DataNotMatchException(message=msg)
        data = data.drop(schema.primary_field.name, axis=1)

    infer_fields, tmp_fields, is_data_frame = parse_fields_from_data(schema, data)
    check_infer_fields_valid(infer_fields, tmp_fields, is_data_frame)


def check_upsert_schema(schema: CollectionSchema, data: Union[List[List], pd.DataFrame]):
    if schema is None:
        raise SchemaNotReadyException(message="Schema shouldn't be None")
    if schema.auto_id:
        raise UpsertAutoIDTrueException(message=ExceptionsMessage.UpsertAutoIDTrue)

    infer_fields, tmp_fields, is_data_frame = parse_fields_from_data(schema, data)
    check_infer_fields_valid(infer_fields, tmp_fields, is_data_frame)


def parse_fields_from_data(schema: CollectionSchema, data: Union[List[List], pd.DataFrame]):
    if not isinstance(data, (pd.DataFrame, list)):
        raise DataTypeNotSupportException(
            message="The type of data should be list or pandas.DataFrame"
        )

    if isinstance(data, pd.DataFrame):
        return parse_fields_from_dataframe(schema, data)

    tmp_fields = copy.deepcopy(schema.fields)
    for i, field in enumerate(tmp_fields):
        if field.is_primary and field.auto_id:
            tmp_fields.pop(i)

    infer_fields = []
    for i, field in enumerate(tmp_fields):
        try:
            d = data[i]
            # if pass in None, considering to be passed in order according to the schema
            if d is None:
                infer_fields.append(FieldSchema("", field.dtype))
                continue
            if not is_list_like(d):
                raise DataTypeNotSupportException(message="data should be a list of list")
            try:
                elem = d[0]
                infer_fields.append(FieldSchema("", infer_dtype_bydata(elem)))
            # if pass in [], considering to be passed in order according to the schema
            except IndexError:
                infer_fields.append(FieldSchema("", field.dtype))
        # the last missing part of data is also completed in order according to the schema
        except IndexError:
            infer_fields.append(FieldSchema("", field.dtype))

    index = len(tmp_fields)
    while index < len(data):
        fields = FieldSchema("", infer_dtype_bydata(data[index][0]))
        infer_fields.append(fields)
        index = index + 1

    return infer_fields, tmp_fields, False


def parse_fields_from_dataframe(schema: CollectionSchema, df: pd.DataFrame):
    col_names, data_types, column_params_map = prepare_fields_from_dataframe(df)
    tmp_fields = copy.deepcopy(schema.fields)
    for i, field in enumerate(schema.fields):
        if field.is_primary and field.auto_id:
            tmp_fields.pop(i)
    infer_fields = []
    for field in tmp_fields:
        # if no data pass in, considering to be passed in order according to the schema
        if field.name not in col_names:
            field_schema = FieldSchema(field.name, field.dtype)
            col_names.append(field.name)
            data_types.append(field.dtype)
            infer_fields.append(field_schema)
        else:
            type_params = column_params_map.get(field.name, {})
            field_schema = FieldSchema(
                field.name, data_types[col_names.index(field.name)], **type_params
            )
            infer_fields.append(field_schema)

    infer_name = [f.name for f in infer_fields]
    for name, dtype in zip(col_names, data_types):
        if name not in infer_name:
            type_params = column_params_map.get(name, {})
            field_schema = FieldSchema(name, dtype, **type_params)
            infer_fields.append(field_schema)

    return infer_fields, tmp_fields, True


def construct_fields_from_dataframe(df: pd.DataFrame) -> List[FieldSchema]:
    col_names, data_types, column_params_map = prepare_fields_from_dataframe(df)
    fields = []
    for name, dtype in zip(col_names, data_types):
        type_params = column_params_map.get(name, {})
        field_schema = FieldSchema(name, dtype, **type_params)
        fields.append(field_schema)

    return fields


def prepare_fields_from_dataframe(df: pd.DataFrame):
    d_types = list(df.dtypes)
    data_types = list(map(map_numpy_dtype_to_datatype, d_types))
    col_names = list(df.columns)

    column_params_map = {}

    if DataType.UNKNOWN in data_types:
        if len(df) == 0:
            raise CannotInferSchemaException(message=ExceptionsMessage.DataFrameInvalid)
        values = df.head(1).to_numpy()[0]
        for i, dtype in enumerate(data_types):
            if dtype == DataType.UNKNOWN:
                new_dtype = infer_dtype_bydata(values[i])
                if new_dtype in (DataType.BINARY_VECTOR, DataType.FLOAT_VECTOR):
                    vector_type_params = {}
                    if new_dtype == DataType.BINARY_VECTOR:
                        vector_type_params["dim"] = len(values[i]) * 8
                    else:
                        vector_type_params["dim"] = len(values[i])
                    column_params_map[col_names[i]] = vector_type_params
                data_types[i] = new_dtype

    if DataType.UNKNOWN in data_types:
        raise CannotInferSchemaException(message=ExceptionsMessage.DataFrameInvalid)

    return col_names, data_types, column_params_map


def check_infer_fields_valid(
    infer_fields: List[FieldSchema],
    tmp_fields: List,
    is_data_frame: bool,
):
    if len(infer_fields) != len(tmp_fields):
        i_name = [f.name for f in infer_fields]
        t_name = [f.name for f in tmp_fields]
        raise DataNotMatchException(
            message=f"The fields don't match with schema fields, expected: {t_name}, got {i_name}"
        )

    for x, y in zip(infer_fields, tmp_fields):
        if x.dtype != y.dtype:
            msg = (
                f"The data type of field {y.name} doesn't match, "
                f"expected: {y.dtype.name}, got {x.dtype.name}"
            )
            raise DataNotMatchException(message=msg)
        if is_data_frame and x.name != y.name:
            raise DataNotMatchException(
                message=f"The name of field don't match, expected: {y.name}, got {x.name}"
            )


def check_schema(schema: CollectionSchema):
    if schema is None:
        raise SchemaNotReadyException(message=ExceptionsMessage.NoSchema)
    if len(schema.fields) < 1:
        raise SchemaNotReadyException(message=ExceptionsMessage.EmptySchema)
    vector_fields = []
    for field in schema.fields:
        if field.dtype in (DataType.FLOAT_VECTOR, DataType.BINARY_VECTOR):
            vector_fields.append(field.name)
    if len(vector_fields) < 1:
        raise SchemaNotReadyException(message=ExceptionsMessage.NoVector)


def infer_default_value_bydata(data: Any):
    if data is None:
        return None
    default_data = schema_types.ValueField()
    d_type = DataType.UNKNOWN
    if is_scalar(data):
        d_type = infer_dtype_by_scaladata(data)
    if d_type is DataType.BOOL:
        default_data.bool_data = data
    elif d_type in (DataType.INT8, DataType.INT16, DataType.INT32):
        default_data.int_data = data
    elif d_type is DataType.INT64:
        default_data.long_data = data
    elif d_type is DataType.FLOAT:
        default_data.float_data = data
    elif d_type is DataType.DOUBLE:
        default_data.double_data = data
    elif d_type is DataType.VARCHAR:
        default_data.string_data = data
    else:
        raise ParamError(message=f"Default value unsupported data type: {d_type}")
    return default_data
