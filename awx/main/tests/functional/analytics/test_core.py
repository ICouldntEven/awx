import importlib
import json
import os
import tarfile
import tempfile
from unittest import mock
import pytest

from django.conf import settings
from django.test.utils import override_settings
from awx.main.analytics import gather, register, ship


@register('example', '1.0')
def example(since, **kwargs):
    return {'awx': 123}


@register('bad_json', '1.0')
def bad_json(since, **kwargs):
    return set()


@register('throws_error', '1.0')
def throws_error(since, **kwargs):
    raise ValueError()


def _valid_license():
    pass


@pytest.fixture
def mock_valid_license():
    with mock.patch('awx.main.analytics.core._valid_license') as license:
        license.return_value = True
        yield license


@pytest.mark.django_db
def test_gather(mock_valid_license):
    settings.INSIGHTS_TRACKING_STATE = True

    tgzfiles = gather(module=importlib.import_module(__name__), collection_type='dry-run')
    files = {}
    with tarfile.open(tgzfiles[0], "r:gz") as archive:
        for member in archive.getmembers():
            files[member.name] = archive.extractfile(member)

        # functions that returned valid JSON should show up
        assert './example.json' in files.keys()
        assert json.loads(files['./example.json'].read()) == {'awx': 123}

        # functions that don't return serializable objects should not
        assert './bad_json.json' not in files.keys()
        assert './throws_error.json' not in files.keys()
    try:
        for tgz in tgzfiles:
            os.remove(tgz)
    except Exception:
        pass


@pytest.fixture
def temp_analytic_tar():
    # Create a temporary file and yield its path
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        temp_file.write(b"data")
        temp_file_path = temp_file.name
    yield temp_file_path
    # Clean up the temporary file after the test
    os.remove(temp_file_path)


@pytest.fixture
def mock_analytic_post():
    # Patch the Session.post method to return a mock response with status_code 200
    with mock.patch('awx.main.analytics.core.requests.Session.post', return_value=mock.Mock(status_code=200)) as mock_post:
        yield mock_post


@pytest.mark.parametrize(
    "setting_map, expected_result, expected_auth",
    [
        # Test case 1: Valid Red Hat credentials
        (
            {
                'REDHAT_USERNAME': 'redhat_user',
                'REDHAT_PASSWORD': 'redhat_pass',  # NOSONAR
                'SUBSCRIPTION_USERNAME': None,
                'SUBSCRIPTION_PASSWORD': None,
            },
            True,
            ('redhat_user', 'redhat_pass'),
        ),
        # Test case 2: Valid Subscription credentials
        (
            {
                'REDHAT_USERNAME': None,
                'REDHAT_PASSWORD': None,
                'SUBSCRIPTION_USERNAME': 'subs_user',
                'SUBSCRIPTION_PASSWORD': 'subs_pass',  # NOSONAR
            },
            True,
            ('subs_user', 'subs_pass'),
        ),
        # Test case 3: No credentials
        (
            {
                'REDHAT_USERNAME': None,
                'REDHAT_PASSWORD': None,
                'SUBSCRIPTION_USERNAME': None,
                'SUBSCRIPTION_PASSWORD': None,
            },
            False,
            None,  # No request should be made
        ),
        # Test case 4: Mixed credentials
        (
            {
                'REDHAT_USERNAME': None,
                'REDHAT_PASSWORD': 'redhat_pass',  # NOSONAR
                'SUBSCRIPTION_USERNAME': 'subs_user',
                'SUBSCRIPTION_PASSWORD': None,
            },
            False,
            None,  # Invalid, no request should be made
        ),
    ],
)
@pytest.mark.django_db
def test_ship_credential(setting_map, expected_result, expected_auth, temp_analytic_tar, mock_analytic_post):
    with override_settings(**setting_map):
        result = ship(temp_analytic_tar)

        assert result == expected_result
        if expected_auth:
            mock_analytic_post.assert_called_once()
            assert mock_analytic_post.call_args[1]['auth'] == expected_auth
        else:
            mock_analytic_post.assert_not_called()
