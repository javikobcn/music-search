from ..slskd import SlskdClient


def download(search_result):

    client = SlskdClient()

    client.login()

    return client.download(
        username=search_result.username,
        filename=search_result.filename,
        size=search_result.size,
    )