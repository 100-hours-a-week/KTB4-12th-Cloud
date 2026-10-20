// V1 Nginx의 주요 API 전달 경로 유지. Body/Query/인증 헤더 변경 없음.
function handler(event) {
    var request = event.request;
    if (request.uri === '/api/health') {
        request.uri = '/actuator/health';
    } else if (request.uri === '/api') {
        request.uri = '/';
    } else if (request.uri.indexOf('/api/') === 0) {
        request.uri = request.uri.slice(4);
    }
    return request;
}
