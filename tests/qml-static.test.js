// Static guardrails supplement executed model/service and rendered QML tests.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = f=>fs.readFileSync(path.join(__dirname,'..',f),'utf8');
test('dynamic vault labels, codes and errors are rendered as plain text',()=>{
  const panel=read('Panel.qml');
  const blocks=panel.match(/Text\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}/g)||[];
  const dynamic=blocks.filter(b=>/modelData|shown\.|statusLine|root\.feedback|remaining ===/.test(b));
  assert.ok(dynamic.length>=6,'expected vault/status text elements');
  for(const block of dynamic) assert.match(block,/textFormat:\s*Text\.PlainText/);
});
test('QML never reads or manages clipboard data',()=>{
  for(const file of ['Panel.qml','Service.qml']) assert.doesNotMatch(read(file),/wl-paste|wl-copy|_clipboardCode|_pendingCopy/);
});
test('QML and UI model expose no network API',()=>{
  for(const file of ['Panel.qml','BarWidget.qml','Service.qml','ProtonAuthIcon.qml','Model.js'])
    assert.doesNotMatch(read(file),/XMLHttpRequest|Qt\.openUrlExternally|WebSocket|https?:\/\//,file);
});
test('helper output has a streaming cap, watchdog and no retained collector',()=>{
  const source=read('Service.qml');
  assert.doesNotMatch(source,/StdioCollector|stderr\s*:/);
  assert.match(source,/_out\.length \+ data\.length > 1024 \* 1024/);
  assert.match(source,/running:\s*listProcess\.running/);
});
