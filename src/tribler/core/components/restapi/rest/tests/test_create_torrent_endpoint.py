from unittest.mock import Mock

import pytest
from ipv8.util import succeed

from tribler.core.components.libtorrent.restapi.create_torrent_endpoint import CreateTorrentEndpoint
from tribler.core.components.libtorrent.settings import DownloadDefaultsSettings
from tribler.core.components.restapi.rest.base_api_test import do_request
from tribler.core.tests.tools.common import TESTS_DATA_DIR


# pylint: disable=redefined-outer-name


@pytest.fixture
def endpoint(download_manager):
    return CreateTorrentEndpoint(download_manager)


async def test_create_torrent(rest_api, tmp_path, download_manager):
    """
    Testing whether the API returns a proper base64 encoded torrent
    """

    def fake_create_torrent_file(*_, **__):
        with open(TESTS_DATA_DIR / "bak_single.torrent", mode='rb') as torrent_file:
            encoded_metainfo = torrent_file.read()
        return succeed({"metainfo": encoded_metainfo, "base_dir": str(tmp_path)})

    download_manager.download_defaults = DownloadDefaultsSettings()
    download_manager.create_torrent_file = fake_create_torrent_file
    download_manager.start_download = start_download = Mock()

    torrent_path = tmp_path / "video.avi.torrent"
    post_data = {
        "files": [str(torrent_path / "video.avi"),
                  str(torrent_path / "video.avi.torrent")],
        "description": "Video of my cat",
        "trackers": "http://localhost/announce",
        "name": "test_torrent",
        "export_dir": str(tmp_path)
    }
    response_dict = await do_request(rest_api, 'createtorrent?download=1', expected_code=200, request_type='POST',
                                     post_data=post_data)
    assert response_dict["torrent"]
    assert start_download.call_args[1]['config'].get_hops() == DownloadDefaultsSettings(
    ).number_hops  # pylint: disable=unsubscriptable-object


async def test_create_torrent_io_error(rest_api, download_manager):
    """
    Testing whether the API returns a formatted 500 error if IOError is raised
    """

    def fake_create_torrent_file(*_, **__):
        raise OSError("test")

    download_manager.create_torrent_file = fake_create_torrent_file

    post_data = {
        "files": ["non_existing_file.avi"]
    }
    error_response = await do_request(rest_api, 'createtorrent', expected_code=500, request_type='POST',
                                      post_data=post_data)
    expected_response = {
        "error": {
            "code": "OSError",
            "handled": True,
            "message": "test"
        }
    }
    assert expected_response == error_response


async def test_create_torrent_missing_files_parameter(rest_api):
    expected_json = {"error": "files parameter missing"}
    await do_request(rest_api, 'createtorrent', expected_code=400, expected_json=expected_json, request_type='POST')
