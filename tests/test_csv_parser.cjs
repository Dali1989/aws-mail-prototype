const assert=require('node:assert/strict');
const {parseCsv}=require('../src/csv-parser.js');
const head='companyName,contactName,email\n';
const allowed=['test@example.com','other@example.com'];
let count=0;
function test(name,run){run();count++;console.log('PASS '+name);}
test('UTF-8 BOM and CRLF',()=>assert.deepEqual(parseCsv('\uFEFF'+head.replace('\n','\r\n')+'富士山ソフトウエア株式会社,佐藤,test@example.com\r\n',allowed),[{companyName:'富士山ソフトウエア株式会社',contactName:'佐藤',email:'test@example.com'}]));
test('quoted comma and escaped quote',()=>assert.equal(parseCsv(head+'"Test, ""Co""",Sato,test@example.com',allowed)[0].companyName,'Test, "Co"'));
test('two recipients',()=>assert.equal(parseCsv(head+'A,B,test@example.com\nC,D,other@example.com',allowed).length,2));
test('bad header',()=>assert.throws(()=>parseCsv('email,name\nx,y',allowed)));
test('empty rows',()=>assert.throws(()=>parseCsv(head,allowed)));
test('duplicate addresses',()=>assert.throws(()=>parseCsv(head+'A,B,test@example.com\nC,D,test@example.com',allowed)));
test('unknown address',()=>assert.throws(()=>parseCsv(head+'A,B,external@example.com',allowed)));
test('missing and extra column',()=>{assert.throws(()=>parseCsv(head+'A,B',allowed));assert.throws(()=>parseCsv(head+'A,B,test@example.com,extra',allowed));});
test('malformed quotes',()=>{assert.throws(()=>parseCsv(head+'"A,B,test@example.com',allowed));assert.throws(()=>parseCsv(head+'"A"x,B,test@example.com',allowed));});
test('multiline name is rejected',()=>assert.throws(()=>parseCsv(head+'"A\nB",C,test@example.com',allowed)));
test('configured row limit',()=>assert.throws(()=>parseCsv(head+'A,B,test@example.com\nC,D,other@example.com',allowed,1)));
test('customer text stays literal',()=>assert.equal(parseCsv(head+'{{contactName}},佐藤,test@example.com',allowed)[0].companyName,'{{contactName}}'));
console.log(`${count} parser tests passed; no AWS calls.`);
