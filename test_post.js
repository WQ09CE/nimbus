const http = require('http');

const data = JSON.stringify({
  method: 'agent/run',
  body: {
    properties: { model: 'claude-3.5-sonnet' }
  }
});

const req = http.request({
  hostname: 'localhost',
  port: 3000,
  path: '/api/copilotkit',
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Content-Length': data.length
  }
}, res => {
  res.on('data', d => process.stdout.write(d));
});

req.write(data);
req.end();
