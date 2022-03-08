import json
from unittest.mock import patch

from django.core.cache import cache

import pytest
from sqlalchemy_filters.exceptions import BadSortFormat, SortFieldNotFound

from db.functions.exceptions import UnknownDBFunctionID
from db.functions.base import Identity
from db.records.exceptions import BadGroupFormat, GroupFieldNotFound
from db.records.operations.group import GroupBy
from mathesar import models
from mathesar.functions.operations.convert import rewrite_db_function_spec_column_ids_to_names
from mathesar.api.exceptions.error_codes import ErrorCodes


def _get_columns_by_name(table, name_list):
    columns_by_name_dict = {
        col.name: col for col in models.Column.objects.filter(table=table) if col.name in name_list
    }
    return [columns_by_name_dict[col_name] for col_name in name_list]


def test_record_list(create_table, client):
    """
    Desired format:
    {
        "count": 25,
        "results": [
            {
                "id": 1,
                "Center": "NASA Kennedy Space Center",
                "Status": "Application",
                "Case Number": "KSC-12871",
                "Patent Number": "0",
                "Application SN": "13/033,085",
                "Title": "Polyimide Wire Insulation Repair System",
                "Patent Expiration Date": ""
            },
            {
                "id": 2,
                "Center": "NASA Ames Research Center",
                "Status": "Issued",
                "Case Number": "ARC-14048-1",
                "Patent Number": "5694939",
                "Application SN": "08/543,093",
                "Title": "Autogenic-Feedback Training Exercise Method & System",
                "Patent Expiration Date": "10/03/2015"
            },
            etc.
        ]
    }
    """
    table_name = 'NASA Record List'
    table = create_table(table_name)

    response = client.get(f'/api/db/v0/tables/{table.id}/records/')
    assert response.status_code == 200

    response_data = response.json()
    record_data = response_data['results'][0]
    assert response_data['count'] == 1393
    assert len(response_data['results']) == 50
    for column_name in table.sa_column_names:
        assert column_name in record_data


serialization_test_list = [
    ("TIME WITH TIME ZONE", "12:30:10.0+01:00"),
    ("TIMESTAMP WITHOUT TIME ZONE", "2000-05-23T12:30:10.0 AD"),
    ("MONEY", "$5.00"),
]


@pytest.mark.parametrize("type_, value", serialization_test_list)
def test_record_serialization(empty_nasa_table, client, type_, value):
    col_name = "TEST COL"
    empty_nasa_table.add_column({"name": col_name, "type": type_})
    empty_nasa_table.create_record_or_records([{col_name: value}])

    response = client.get(f'/api/db/v0/tables/{empty_nasa_table.id}/records/')
    response_data = response.json()

    assert response.status_code == 200
    assert response_data["results"][0][col_name] == value


def test_record_list_filter(create_table, client):
    table_name = 'NASA Record List Filter'
    table = create_table(table_name)
    column_names_to_ids = table.get_dj_column_name_to_id_mapping()

    filter = {"or": [
        {"and": [
            {"equal": [
                {"column_id": [column_names_to_ids["Center"]]},
                {"literal": ["NASA Ames Research Center"]}
            ]},
            {"equal": [
                {"column_id": [column_names_to_ids["Case Number"]]},
                {"literal": ["ARC-14048-1"]}
            ]},
        ]},
        {"and": [
            {"equal": [
                {"column_id": [column_names_to_ids["Center"]]},
                {"literal": ["NASA Kennedy Space Center"]}
            ]},
            {"equal": [
                {"column_id": [column_names_to_ids["Case Number"]]},
                {"literal": ["KSC-12871"]}
            ]},
        ]},
    ]}
    json_filter = json.dumps(filter)

    with patch.object(
        models, "db_get_records", side_effect=models.db_get_records
    ) as mock_get:
        response = client.get(
            f'/api/db/v0/tables/{table.id}/records/?filter={json_filter}'
        )

    assert response.status_code == 200
    response_data = response.json()
    assert response_data['count'] == 2
    assert len(response_data['results']) == 2
    assert mock_get.call_args is not None
    column_ids_to_names = table.get_dj_column_id_to_name_mapping()
    processed_filter = rewrite_db_function_spec_column_ids_to_names(
        column_ids_to_names=column_ids_to_names,
        spec=filter,
    )
    assert mock_get.call_args[1]['filter'] == processed_filter


def test_record_list_duplicate_rows_only(create_table, client):
    table_name = 'NASA Record List Filter Duplicates'
    table = create_table(table_name)

    duplicate_only = ['Patent Expiration Date']
    json_duplicate_only = json.dumps(duplicate_only)

    with patch.object(models, "db_get_records", return_value=[]) as mock_get:
        client.get(f'/api/db/v0/tables/{table.id}/records/?duplicate_only={json_duplicate_only}')
    assert mock_get.call_args is not None
    assert mock_get.call_args[1]['duplicate_only'] == duplicate_only


def test_record_db_function_and_deduplicate(create_table, client):
    table_name = 'NASA Record List Filter Duplicates'
    table = create_table(table_name)

    column_id = table.get_dj_columns()[1].id
    db_function = {Identity.id: [{'column_id': [column_id]}]}
    db_function_json = json.dumps(db_function)
    deduplicate = True
    deduplicate_json = json.dumps(deduplicate)
    response = client.get(f'/api/db/v0/tables/{table.id}/records/?db_function={db_function_json}&deduplicate={deduplicate_json}')
    assert response.status_code == 200
    assert response.data['count'] == 11
    assert len(response.data['results']) == 11


def test_filter_with_added_columns(create_table, client):
    cache.clear()
    table_name = 'NASA Record List Filter'
    table = create_table(table_name)

    columns_to_add = [
        {
            'name': 'Published',
            'type': 'BOOLEAN',
            'default_value': True,
            'row_values': {1: False, 2: False, 3: None}
        }
    ]

    operators_and_expected_values = [
        (
            lambda new_column_id, value: {"not": [{"equal": [{"column_id": [new_column_id]}, {"literal": [value]}]}]},
            True, 2),
        (
            lambda new_column_id, value: {"equal": [{"column_id": [new_column_id]}, {"literal": [value]}]},
            False, 2),
        (
            lambda new_column_id, _: {"empty": [{"column_id": [new_column_id]}]},
            None, 1394),
        (
            lambda new_column_id, _: {"not": [{"empty": [{"column_id": [new_column_id]}]}]},
            None, 49),
    ]

    for new_column in columns_to_add:
        new_column_name = new_column.get("name")
        new_column_type = new_column.get("type")
        table.add_column({"name": new_column_name, "type": new_column_type})
        row_values_list = []

        response_data = client.get(f'/api/db/v0/tables/{table.id}/records/').json()
        existing_records = response_data['results']

        for row_number, row in enumerate(existing_records, 1):
            row_value = new_column.get("row_values").get(row_number, new_column.get("default_value"))
            row_values_list.append({new_column_name: row_value})

        table.create_record_or_records(row_values_list)
        column_ids_to_names = table.get_dj_column_id_to_name_mapping()

        column_names_to_ids = table.get_dj_column_name_to_id_mapping()
        new_column_id = column_names_to_ids[new_column_name]

        for filter_lambda, value, expected in operators_and_expected_values:
            filter = filter_lambda(new_column_id, value)
            json_filter = json.dumps(filter)

            with patch.object(
                models, "db_get_records", side_effect=models.db_get_records
            ) as mock_get:
                response = client.get(
                    f'/api/db/v0/tables/{table.id}/records/?filter={json_filter}'
                )
                response_data = response.json()

            num_results = expected
            if expected > 50:
                num_results = 50
            assert response.status_code == 200
            assert response_data['count'] == expected
            assert len(response_data['results']) == num_results
            assert mock_get.call_args is not None
            processed_filter = rewrite_db_function_spec_column_ids_to_names(
                column_ids_to_names=column_ids_to_names,
                spec=filter,
            )
            assert mock_get.call_args[1]['filter'] == processed_filter


def test_record_list_sort(create_table, client):
    table_name = 'NASA Record List Order'
    table = create_table(table_name)

    order_by = [
        {'field': 'Center', 'direction': 'desc'},
        {'field': 'Case Number', 'direction': 'asc'},
    ]
    json_order_by = json.dumps(order_by)

    with patch.object(
        models, "db_get_records", side_effect=models.db_get_records
    ) as mock_get:
        response = client.get(
            f'/api/db/v0/tables/{table.id}/records/?order_by={json_order_by}'
        )
        response_data = response.json()

    assert response.status_code == 200
    assert response_data['count'] == 1393
    assert len(response_data['results']) == 50

    assert mock_get.call_args is not None
    assert mock_get.call_args[1]['order_by'] == order_by


grouping_params = [
    (
        'NASA Record List Group Single',
        {'columns': ['Center']},
        [
            {
                'count': 87,
                'first_value': {'Center': 'NASA Kennedy Space Center'},
                'last_value': {'Center': 'NASA Kennedy Space Center'},
                'result_indices': [0]
            }, {
                'count': 138,
                'first_value': {'Center': 'NASA Ames Research Center'},
                'last_value': {'Center': 'NASA Ames Research Center'},
                'result_indices': [
                    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17,
                    18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 31, 32, 33,
                    34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48,
                    49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63,
                    64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78,
                    79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93,
                    94, 95, 96, 97, 98, 99,
                ]
            }, {
                'count': 21,
                'first_value': {'Center': 'NASA Armstrong Flight Research Center'},
                'last_value': {'Center': 'NASA Armstrong Flight Research Center'},
                'result_indices': [30]
            },
        ],
    ),
    (
        'NASA Record List Group Single Percentile',
        {'columns': ['Center'], 'mode': 'percentile', 'num_groups': 5},
        [
            {
                'count': 87,
                'first_value': {'Center': 'NASA Kennedy Space Center'},
                'last_value': {'Center': 'NASA Kennedy Space Center'},
                'result_indices': [0]
            }, {
                'count': 159,
                'first_value': {'Center': 'NASA Ames Research Center'},
                'last_value': {'Center': 'NASA Armstrong Flight Research Center'},
                'result_indices': [
                    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17,
                    18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32,
                    33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47,
                    48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62,
                    63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77,
                    78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92,
                    93, 94, 95, 96, 97, 98, 99
                ],
            },
        ],
    ),
    (
        'NASA Record List Group Multi',
        {'columns': ['Center', 'Status']},
        [
            {
                'count': 29,
                'first_value': {
                    'Center': 'NASA Kennedy Space Center', 'Status': 'Application'
                },
                'last_value': {
                    'Center': 'NASA Kennedy Space Center', 'Status': 'Application'
                },
                'result_indices': [0]
            }, {
                'count': 100,
                'first_value': {
                    'Center': 'NASA Ames Research Center', 'Status': 'Issued'
                },
                'last_value': {
                    'Center': 'NASA Ames Research Center', 'Status': 'Issued'
                }, 'result_indices': [
                    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17,
                    18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 31, 32, 33,
                    34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48,
                    49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63,
                    64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78,
                    79, 80, 81, 82, 83, 84, 85, 88, 90, 91, 92, 94, 96, 98, 99
                ]
            }, {
                'count': 12,
                'first_value': {
                    'Center': 'NASA Armstrong Flight Research Center', 'Status': 'Issued'
                },
                'last_value': {
                    'Center': 'NASA Armstrong Flight Research Center', 'Status': 'Issued'
                },
                'result_indices': [30]
            }, {
                'count': 38,
                'first_value': {
                    'Center': 'NASA Ames Research Center', 'Status': 'Application'
                },
                'last_value': {
                    'Center': 'NASA Ames Research Center', 'Status': 'Application'
                }, 'result_indices': [86, 87, 89, 93, 95, 97]
            },
        ],
    ),
    (
        'NASA Record List Group Multi Percentile',
        {'columns': ['Center', 'Status'], 'mode': 'percentile', 'num_groups': 5},
        [
            {
                'count': 197,
                'first_value': {
                    'Center': 'NASA Kennedy Space Center', 'Status': 'Application'
                },
                'last_value': {
                    'Center': 'NASA Langley Research Center', 'Status': 'Application'
                },
                'result_indices': [0]
            }, {
                'count': 159,
                'first_value': {
                    'Center': 'NASA Ames Research Center', 'Status': 'Application'
                },
                'last_value': {
                    'Center': 'NASA Armstrong Flight Research Center', 'Status': 'Issued'
                },
                'result_indices': [
                    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17,
                    18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32,
                    33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47,
                    48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62,
                    63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77,
                    78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92,
                    93, 94, 95, 96, 97, 98, 99
                ],
            },
        ],
    ),
]


def test_null_error_record_create(create_table, client):
    table_name = 'NASA Record Create'
    table = create_table(table_name)
    column = _get_columns_by_name(table, ['Case Number'])[0]
    data = {"nullable": False}
    client.patch(
        f"/api/db/v0/tables/{table.id}/columns/{column.id}/", data=data
    )
    data = {
        'Center': 'NASA Example Space Center',
        'Status': 'Application',
        'Case Number': None,
        'Patent Number': '01234',
        'Application SN': '01/000,001',
        'Title': 'Example Patent Name',
        'Patent Expiration Date': ''
    }
    response = client.post(f'/api/db/v0/tables/{table.id}/records/', data=data)
    record_data = response.json()
    assert response.status_code == 400
    assert 'null value in column "Case Number"' in record_data[0]['message']
    assert column.id == record_data[0]['detail']['column_id']


@pytest.mark.parametrize('table_name,grouping,expected_groups', grouping_params)
def test_record_list_groups(
        table_name, grouping, expected_groups, create_table, client,
):
    table = create_table(table_name)
    order_by = [
        {'field': 'id', 'direction': 'asc'},
    ]
    json_order_by = json.dumps(order_by)
    json_grouping = json.dumps(grouping)
    limit = 100
    query_str = f'grouping={json_grouping}&order_by={json_order_by}&limit={limit}'

    response = client.get(f'/api/db/v0/tables/{table.id}/records/?{query_str}')
    response_data = response.json()

    assert response.status_code == 200
    assert response_data['count'] == 1393
    assert len(response_data['results']) == limit

    group_by = GroupBy(**grouping)
    grouping_dict = response_data['grouping']
    assert grouping_dict['columns'] == list(group_by.columns)
    assert grouping_dict['mode'] == group_by.mode
    assert grouping_dict['num_groups'] == group_by.num_groups
    assert grouping_dict['ranged'] == group_by.ranged
    assert grouping_dict['groups'] == expected_groups


def test_record_list_pagination_limit(create_table, client):
    table_name = 'NASA Record List Pagination Limit'
    table = create_table(table_name)

    response = client.get(f'/api/db/v0/tables/{table.id}/records/?limit=5')
    response_data = response.json()
    record_data = response_data['results'][0]

    assert response.status_code == 200
    assert response_data['count'] == 1393
    assert len(response_data['results']) == 5
    for column_name in table.sa_column_names:
        assert column_name in record_data


def test_record_list_pagination_offset(create_table, client):
    table_name = 'NASA Record List Pagination Offset'
    table = create_table(table_name)

    response_1 = client.get(f'/api/db/v0/tables/{table.id}/records/?limit=5&offset=5')
    response_1_data = response_1.json()
    record_1_data = response_1_data['results'][0]
    response_2 = client.get(f'/api/db/v0/tables/{table.id}/records/?limit=5&offset=10')
    response_2_data = response_2.json()
    record_2_data = response_2_data['results'][0]

    assert response_1.status_code == 200
    assert response_2.status_code == 200
    assert response_1_data['count'] == 1393
    assert response_2_data['count'] == 1393
    assert len(response_1_data['results']) == 5
    assert len(response_2_data['results']) == 5

    assert record_1_data['id'] != record_2_data['id']
    assert record_1_data['Case Number'] != record_2_data['Case Number']
    assert record_1_data['Patent Number'] != record_2_data['Patent Number']
    assert record_1_data['Application SN'] != record_2_data['Application SN']


def test_record_detail(create_table, client):
    table_name = 'NASA Record Detail'
    table = create_table(table_name)
    record_id = 1
    record = table.get_record(record_id)

    response = client.get(f'/api/db/v0/tables/{table.id}/records/{record_id}/')
    record_data = response.json()
    record_as_dict = record._asdict()

    assert response.status_code == 200
    for column_name in table.sa_column_names:
        assert column_name in record_data
        assert record_as_dict[column_name] == record_data[column_name]


def test_record_create(create_table, client):
    table_name = 'NASA Record Create'
    table = create_table(table_name)
    records = table.get_records()
    original_num_records = len(records)

    data = {
        'Center': 'NASA Example Space Center',
        'Status': 'Application',
        'Case Number': 'ESC-0000',
        'Patent Number': '01234',
        'Application SN': '01/000,001',
        'Title': 'Example Patent Name',
        'Patent Expiration Date': ''
    }
    response = client.post(f'/api/db/v0/tables/{table.id}/records/', data=data)
    record_data = response.json()

    assert response.status_code == 201
    assert len(table.get_records()) == original_num_records + 1
    for column_name in table.sa_column_names:
        assert column_name in record_data
        if column_name in data:
            assert data[column_name] == record_data[column_name]


def test_record_partial_update(create_table, client):
    table_name = 'NASA Record Patch'
    table = create_table(table_name)
    records = table.get_records()
    record_id = records[0]['id']

    original_response = client.get(f'/api/db/v0/tables/{table.id}/records/{record_id}/')
    original_data = original_response.json()

    data = {
        'Center': 'NASA Example Space Center',
        'Status': 'Example',
    }
    response = client.patch(f'/api/db/v0/tables/{table.id}/records/{record_id}/', data=data)
    record_data = response.json()

    assert response.status_code == 200
    for column_name in table.sa_column_names:
        assert column_name in record_data
        if column_name in data and column_name not in ['Center', 'Status']:
            assert original_data[column_name] == record_data[column_name]
        elif column_name == 'Center':
            assert original_data[column_name] != record_data[column_name]
            assert record_data[column_name] == 'NASA Example Space Center'
        elif column_name == 'Status':
            assert original_data[column_name] != record_data[column_name]
            assert record_data[column_name] == 'Example'


def test_record_delete(create_table, client):
    table_name = 'NASA Record Delete'
    table = create_table(table_name)
    records = table.get_records()
    original_num_records = len(records)
    record_id = records[0]['id']

    response = client.delete(f'/api/db/v0/tables/{table.id}/records/{record_id}/')
    assert response.status_code == 204
    assert len(table.get_records()) == original_num_records - 1


def test_record_update(create_table, client):
    table_name = 'NASA Record Put'
    table = create_table(table_name)
    records = table.get_records()
    record_id = records[0]['id']

    data = {
        'Center': 'NASA Example Space Center',
        'Status': 'Example',
    }
    response = client.put(f'/api/db/v0/tables/{table.id}/records/{record_id}/', data=data)
    assert response.status_code == 405
    assert response.json()[0]['message'] == 'Method "PUT" not allowed.'
    assert response.json()[0]['code'] == ErrorCodes.MethodNotAllowed.value


def test_record_404(create_table, client):
    table_name = 'NASA Record 404'
    table = create_table(table_name)
    records = table.get_records()
    record_id = records[0]['id']

    client.delete(f'/api/db/v0/tables/{table.id}/records/{record_id}/')
    response = client.get(f'/api/db/v0/tables/{table.id}/records/{record_id}/')
    assert response.status_code == 404
    assert response.json()[0]['message'] == 'Not found.'
    assert response.json()[0]['code'] == ErrorCodes.NotFound.value


def test_record_list_filter_exceptions(create_table, client):
    exception = UnknownDBFunctionID
    table_name = f"NASA Record List {exception.__name__}"
    table = create_table(table_name)
    filter = json.dumps({"empty": [{"column_name": ["Center"]}]})
    with patch.object(models, "db_get_records", side_effect=exception):
        response = client.get(
            f'/api/db/v0/tables/{table.id}/records/?filter={filter}'
        )
        response_data = response.json()
    assert response.status_code == 400
    assert len(response_data) == 1
    assert "filter" in response_data[0]['field']


@pytest.mark.parametrize("exception", [BadSortFormat, SortFieldNotFound])
def test_record_list_sort_exceptions(create_table, client, exception):
    table_name = f"NASA Record List {exception.__name__}"
    table = create_table(table_name)
    order_by = json.dumps([{"field": "Center", "direction": "desc"}])
    with patch.object(models, "db_get_records", side_effect=exception):
        response = client.get(
            f'/api/db/v0/tables/{table.id}/records/?order_by={order_by}'
        )
        response_data = response.json()
    assert response.status_code == 400
    assert len(response_data) == 1
    assert "order_by" in response_data[0]['field']


@pytest.mark.parametrize("exception", [BadGroupFormat, GroupFieldNotFound])
def test_record_list_group_exceptions(create_table, client, exception):
    table_name = f"NASA Record List {exception.__name__}"
    table = create_table(table_name)
    group_by = json.dumps({"columns": ["Center"]})
    with patch.object(models, "db_get_records", side_effect=exception):
        response = client.get(
            f'/api/db/v0/tables/{table.id}/records/?grouping={group_by}'
        )
        response_data = response.json()
    assert response.status_code == 400
    assert len(response_data) == 1
    assert "grouping" in response_data[0]['field']
