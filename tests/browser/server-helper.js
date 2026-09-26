const {spawn}=require('node:child_process');
const {existsSync}=require('node:fs');
async function startFixture(script){
  const python=process.env.JEVGAUGE_PYTHON||(['outputs/dashboard-venv/bin/python','.venv/bin/python'].find(existsSync))||'python3';
  const proc=spawn(python,[script],{stdio:['ignore','pipe','pipe']});let stderr='';proc.stderr.on('data',b=>stderr+=b.toString());
  try {
    const url=await new Promise((resolve,reject)=>{let buffer='';const timer=setTimeout(()=>reject(new Error('Fixture server timeout: '+stderr)),8000);proc.on('exit',code=>{clearTimeout(timer);reject(new Error('Fixture failed '+code+': '+stderr));});proc.stdout.on('data',chunk=>{buffer+=chunk.toString();if(buffer.includes('\n')){clearTimeout(timer);resolve(JSON.parse(buffer.split('\n')[0]).url);}});});
    return {url,close:()=>proc.kill('SIGTERM')};
  }catch(error){proc.kill('SIGTERM');throw error;}
}
module.exports={startFixture};
