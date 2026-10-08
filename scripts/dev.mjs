import { spawn, spawnSync } from 'node:child_process';
import { existsSync, copyFileSync, mkdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const windows = process.platform === 'win32';
const backend = join(root, 'backend'), frontend = join(root, 'frontend');
const env = {...process.env, UV_CACHE_DIR: join(backend, '.uv-cache'), HF_HOME: join(backend, 'data', 'hf-cache'), HF_HUB_DISABLE_SYMLINKS_WARNING: '1'};
function run(command, args, cwd) {
  const result = spawnSync(command, args, {cwd, env, stdio:'inherit', shell: windows && command === 'npm', windowsHide: true});
  if (result.error || result.status !== 0) {console.error(result.error?.message || `${command} failed`); process.exit(1);}
}
for (const [directory, filename] of [[backend,'.env'],[frontend,'.env.local']]) {
  if (!existsSync(join(directory,filename))) copyFileSync(join(directory,'.env.example'),join(directory,filename));
}
mkdirSync(join(backend,'data'),{recursive:true});
if (process.argv.includes('--setup') || !existsSync(join(backend,'.venv',windows?'Scripts/python.exe':'bin/python'))) run('uv',['sync','--python','3.12'],backend);
if (process.argv.includes('--setup') || !existsSync(join(frontend,'node_modules'))) run('npm',['ci','--cache','.npm-cache','--no-audit','--no-fund'],frontend);
run(process.execPath,['scripts/monaco.mjs'],frontend);
if (process.argv.includes('--setup')) {console.log('\nSetup complete. Add a free-tier LLM key to backend/.env, then run: node scripts/dev.mjs'); process.exit(0);}
const python = join(backend,'.venv',windows?'Scripts/python.exe':'bin/python');
const children = [spawn(python,['-m','uvicorn','app.api.main:app','--host','127.0.0.1','--port','8000'],{cwd:backend,env,stdio:'inherit',windowsHide:true}),spawn(process.execPath,['node_modules/next/dist/bin/next','dev','--hostname','127.0.0.1'],{cwd:frontend,env,stdio:'inherit',windowsHide:true})];
console.log('\nCodeLens → http://localhost:3000\nAPI docs → http://localhost:8000/docs\nPress Ctrl+C to stop both services.\n');
let stopping = false;
function stop(code=0) {if(stopping)return; stopping=true; for(const child of children) {if(!child.pid)continue; if(windows) spawnSync('taskkill',['/pid',String(child.pid),'/T','/F'],{stdio:'ignore',windowsHide:true}); else child.kill('SIGTERM');} process.exit(code);}
process.on('SIGINT',()=>stop()); process.on('SIGTERM',()=>stop());
for(const child of children) {child.on('error',error=>{console.error(error.message);stop(1);}); child.on('exit',code=>{if(!stopping)stop(code || 0);});}
