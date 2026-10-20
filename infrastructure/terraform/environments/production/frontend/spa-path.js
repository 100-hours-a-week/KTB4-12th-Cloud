// SPA 하위 URL을 index.html로 전달. API 오류를 HTML로 변환하지 않음.
function handler(event) {
    var request = event.request;
    if (request.uri === '/api' || request.uri.indexOf('/api/') === 0) {
        return { statusCode: 404, statusDescription: 'Not Found' };
    }
    var last = request.uri.slice(request.uri.lastIndexOf('/') + 1);
    if (last === '' || last.indexOf('.') === -1) {
        request.uri = '/index.html';
    }
    return request;
}
