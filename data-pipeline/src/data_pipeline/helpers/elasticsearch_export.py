import datetime
from functools import reduce

import elasticsearch
import hail as hl


HAIL_TYPE_TO_ES_TYPE_MAPPING = {
    hl.tint: "integer",
    hl.tint32: "integer",
    hl.tint64: "long",
    hl.tfloat: "double",
    hl.tfloat32: "float",
    hl.tfloat64: "double",
    hl.tstr: "keyword",
    hl.tbool: "boolean",
}


# https://hail.is/docs/0.2/types.html
# https://www.elastic.co/guide/en/elasticsearch/reference/current/mapping-types.html
def _elasticsearch_mapping_for_hail_type(dtype):
    if isinstance(dtype, hl.tstruct):
        return {"properties": {field: _elasticsearch_mapping_for_hail_type(dtype[field]) for field in dtype.fields}}

    if isinstance(dtype, (hl.tarray, hl.tset)):
        element_mapping = _elasticsearch_mapping_for_hail_type(dtype.element_type)

        if isinstance(dtype.element_type, hl.tstruct):
            element_mapping["type"] = "nested"

        return element_mapping

    if isinstance(dtype, hl.tlocus):
        return {"type": "object", "properties": {"contig": {"type": "keyword"}, "position": {"type": "integer"}}}

    if isinstance(dtype, hl.tinterval):
        return {
            "type": "object",
            "properties": {
                "start": _elasticsearch_mapping_for_hail_type(dtype.point_type),
                "end": _elasticsearch_mapping_for_hail_type(dtype.point_type),
                "includes_start": {"type": "boolean"},
                "includes_end": {"type": "boolean"},
            },
        }

    if dtype in HAIL_TYPE_TO_ES_TYPE_MAPPING:
        return {"type": HAIL_TYPE_TO_ES_TYPE_MAPPING[dtype]}

    # tdict, ttuple, tinterval, tcall
    raise NotImplementedError


def _set_field_parameter(mapping, field, parameter, value):
    keys = field.split(".")
    ref = mapping
    for key in keys:
        ref = ref["properties"][key]

    ref[parameter] = value


def elasticsearch_mapping_for_table(table, disable_fields=None, override_types=None):
    """
    Creates an Elasticsearch mapping definition for a Hail table's row value type.

    https://www.elastic.co/guide/en/elasticsearch/guide/current/root-object.html
    """
    mapping = _elasticsearch_mapping_for_hail_type(table.key_by().row_value.dtype)

    if disable_fields:
        for field in disable_fields:
            _set_field_parameter(mapping, field, "enabled", False)

    if override_types:
        for field, field_type in override_types.items():
            _set_field_parameter(mapping, field, "type", field_type)

    return mapping


def struct_to_dict(struct):
    return {k: dict(struct_to_dict(v)) if isinstance(v, hl.utils.Struct) else v for k, v in struct.items()}


def get_index_fields(table, index_fields):
    def _get_index_field(field):
        field_expr = reduce(getattr, field.split("."), table)
        if isinstance(field_expr, hl.CollectionExpression):
            field_expr = hl.set(field_expr)

        return field_expr

    return {field.split(".")[-1]: _get_index_field(field) for field in index_fields}


def export_table_to_elasticsearch(
    table, host, index, *, auth=None, block_size=5000, id_field=None, index_fields=None, num_shards=1,
):
    export_time = datetime.datetime.utcnow()

    table = table.select_globals(exported_at=export_time.isoformat(timespec="seconds"), table_globals=table.globals)
    table = table.key_by()

    if index_fields:
        if id_field and id_field not in [f.split(".")[-1] for f in index_fields]:
            raise RuntimeError("id_field must be included in index_fields")

        table = table.select(**get_index_fields(table, index_fields), value=table.row)
        mapping = elasticsearch_mapping_for_table(table, disable_fields=("value",))
    else:
        mapping = elasticsearch_mapping_for_table(table)

    mapping["_meta"] = struct_to_dict(hl.eval(table.globals))

    # Hard code type name for all indices
    # TODO: Mapping types are removed in ES 7
    # For now, use recommended "_doc" type for all indices
    # https://www.elastic.co/guide/en/elasticsearch/reference/7.x/removal-of-types.html
    type_name = "_doc"

    # https://www.elastic.co/guide/en/elasticsearch/reference/current/index-modules.html#index-modules-settings
    request_body = {
        "mappings": {type_name: mapping},
        "settings": {
            "index.codec": "best_compression",
            "index.mapping.total_fields.limit": 10000,
            "index.number_of_replicas": 0,
            "index.number_of_shards": num_shards,
            "index.refresh_interval": -1,
        },
    }

    es_client = elasticsearch.Elasticsearch(host, port=9200, http_auth=auth)

    index = f"{index}-{export_time.strftime('%Y-%m-%d--%H-%M')}"

    if es_client.indices.exists(index=index):
        es_client.indices.delete(index=index)

    es_client.indices.create(index=index, body=request_body)

    elasticsearch_config = {"es.write.operation": "index"}

    if auth:
        elasticsearch_config["es.net.http.auth.user"] = auth[0]
        elasticsearch_config["es.net.http.auth.pass"] = auth[1]

    if id_field is not None:
        elasticsearch_config["es.mapping.id"] = id_field

    hl.export_elasticsearch(table, host, 9200, index, type_name, block_size, elasticsearch_config, True)

    es_client.indices.forcemerge(index=index)
