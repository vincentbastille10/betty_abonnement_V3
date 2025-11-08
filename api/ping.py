from http import HTTPStatus
def handler(request):
    return (HTTPStatus.OK, {"Content-Type": "application/json"}, b'{"ok":true}')
