const ts = require('typescript');
const fs = require('fs');
const vm = require('vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('path').join(__dirname, '../app/api/gateway/[...path]/route.ts'),'utf8');
const src = ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
let calls=[];
const ctx={exports:{},Response,URL,TextEncoder,process:{env:{NODE_ENV:'production',CODELENS_API_URL:'https://backend.example',CODELENS_ACCESS_TOKEN:'server-test-secret'}},fetch:async(url,opts)=>{calls.push({url,opts});return new Response('data: {"type":"done"}\n\n',{headers:{'content-type':'text/event-stream'}})}};
vm.runInNewContext(src,ctx);
const req=(method='GET',origin='https://app.example',body='')=>({method,headers:new Headers({origin,authorization:'Bearer attacker'}),nextUrl:new URL('https://app.example/api/gateway/api/repos'),signal:new AbortController().signal,text:async()=>body});
const params=path=>({params:Promise.resolve({path:path.split('/')})});
(async()=>{
let r=await ctx.exports.GET(req(),params('api/repos'));assert.equal(r.status,200);assert.equal(calls[0].opts.headers.Authorization,'Bearer server-test-secret');assert.match(await r.text(),/data:/);assert.equal(r.headers.get('cache-control'),'no-store');
r=await ctx.exports.POST(req('POST','https://evil.example','{}'),params('api/repos'));assert.equal(r.status,403);assert.equal(calls.length,1);
r=await ctx.exports.GET(req(),params('https://evil.example'));assert.equal(r.status,404);
r=await ctx.exports.POST(req('POST','https://app.example','a'.repeat(16385)),params('api/repos'));assert.equal(r.status,413);
ctx.fetch=async()=>new Response('private credential error',{status:401});r=await ctx.exports.GET(req(),params('api/repos'));assert.equal(r.status,503);assert.ok(!(await r.text()).includes('credential'));
delete ctx.process.env.CODELENS_ACCESS_TOKEN;r=await ctx.exports.GET(req(),params('api/repos'));assert.equal(r.status,503);
console.log('Seven gateway checks passed.');
})().catch(e=>{console.error(e);process.exit(1)});

