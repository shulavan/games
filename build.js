// 江戸川双六：配布用の軽い sugoroku.html を作ります（コメント・字下げを取るだけ。中身の処理は変えません）
//   使いかた：npm install terser@5.31.0 のあと
//   node build.js sugoroku_src.html sugoroku.html
const {minify}=require('terser');const fs=require('fs');
(async()=>{
 const src=fs.readFileSync(process.argv[2],'utf8');const out=[];let i=0,fails=0,nJs=0,nCss=0;
 const re=/<(script|style)\b([^>]*)>([\s\S]*?)<\/\1>/g;let m;
 const stripHtml=(h)=>h.replace(/<!--(?!\[if)[\s\S]*?-->/g,'').replace(/\n[ \t]+/g,'\n').replace(/\n{2,}/g,'\n');
 while((m=re.exec(src))){
  out.push(stripHtml(src.slice(i,m.index)));i=m.index+m[0].length;
  const [all,tag,attrs,body]=m;
  if(tag==='script'){
   if(/\bsrc=/.test(attrs)||(/type="/.test(attrs)&&!/type="(module|text\/javascript)"/.test(attrs))||!body.trim()){out.push(all);continue;}
   try{const r=await minify(body,{compress:false,mangle:false,module:/type="module"/.test(attrs),format:{comments:false,inline_script:true,ascii_only:false}});
     out.push('<script'+attrs+'>'+r.code+'</script>');nJs++;}
   catch(e){fails++;console.error('JS minify failed',(attrs.match(/id="([^"]+)"/)||[])[1],e.message);out.push(all);}
  }else{
   const css=body.replace(/\/\*[\s\S]*?\*\//g,'').replace(/\s+/g,' ').replace(/\s*([{};,>])\s*/g,'$1').replace(/:\s+/g,':').replace(/;}/g,'}').trim();
   out.push('<style'+attrs+'>'+css+'</style>');nCss++;
  }
 }
 out.push(stripHtml(src.slice(i)));
 const res=out.join('');fs.writeFileSync(process.argv[3],res);
 console.log('js',nJs,'css',nCss,'fail',fails,'bytes',Buffer.byteLength(src),'->',Buffer.byteLength(res));
})();
