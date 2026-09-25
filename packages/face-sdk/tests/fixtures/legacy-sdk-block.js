function mpEncode(value){
  const parts=[];
  mpEnc(value,parts);
  let n=0; for(const p of parts) n+=p.length;
  const out=new Uint8Array(n); let o=0;
  for(const p of parts){ out.set(p,o); o+=p.length; }
  return out;
}
function mpEnc(v,parts){
  if(v===null||v===undefined){ parts.push(new Uint8Array([0xc0])); return; }
  if(typeof v==='boolean'){ parts.push(new Uint8Array([v?0xc3:0xc2])); return; }
  if(typeof v==='number'){ mpNumber(v,parts); return; }
  if(typeof v==='string'){ mpStr(v,parts); return; }
  if(v instanceof Uint8Array){ mpBin(v,parts); return; }
  if(Array.isArray(v)){ mpArray(v,parts); return; }
  mpMap(v,parts);
}
function mpNumber(v,parts){
  if(!Number.isSafeInteger(v)){
    const b=new ArrayBuffer(8); new DataView(b).setFloat64(0,v);
    parts.push(new Uint8Array([0xcb])); parts.push(new Uint8Array(b)); return;
  }
  if(v>=0){
    if(v<=0x7f) parts.push(new Uint8Array([v]));
    else if(v<=0xff) parts.push(new Uint8Array([0xcc,v]));
    else if(v<=0xffff) parts.push(new Uint8Array([0xcd,v>>8,v&255]));
    else if(v<=0xffffffff) parts.push(new Uint8Array([0xce,v>>>24&255,v>>>16&255,v>>>8&255,v&255]));
    else { const hi=Math.floor(v/4294967296);
      parts.push(new Uint8Array([0xcf,hi>>>24&255,hi>>>16&255,hi>>>8&255,hi&255,
        v>>>24&255,v>>>16&255,v>>>8&255,v&255])); }
  }else{
    if(v>=-32) parts.push(new Uint8Array([v+256]));
    else if(v>=-128) parts.push(new Uint8Array([0xd0,v+256]));
    else if(v>=-32768) parts.push(new Uint8Array([0xd1,v>>8&255,v&255]));
    else parts.push(new Uint8Array([0xd2,v>>>24&255,v>>>16&255,v>>>8&255,v&255]));
  }
}
function mpStr(v,parts){
  const b=new TextEncoder().encode(v), n=b.length;
  if(n<=31) parts.push(new Uint8Array([0xa0|n]));
  else if(n<=255) parts.push(new Uint8Array([0xd9,n]));
  else parts.push(new Uint8Array([0xda,n>>8,n&255]));
  parts.push(b);
}
function mpBin(v,parts){
  const n=v.length;
  if(n<=255) parts.push(new Uint8Array([0xc4,n]));
  else if(n<=65535) parts.push(new Uint8Array([0xc5,n>>8,n&255]));
  else parts.push(new Uint8Array([0xc6,n>>>24&255,n>>>16&255,n>>>8&255,n&255]));
  parts.push(v);
}
function mpArray(v,parts){
  const n=v.length;
  if(n<=15) parts.push(new Uint8Array([0x90|n]));
  else parts.push(new Uint8Array([0xdc,n>>8,n&255]));
  for(const x of v) mpEnc(x,parts);
}
function mpMap(v,parts){
  const ks=Object.keys(v), n=ks.length;
  if(n<=15) parts.push(new Uint8Array([0x80|n]));
  else parts.push(new Uint8Array([0xde,n>>8,n&255]));
  for(const k of ks){ mpStr(k,parts); mpEnc(v[k],parts); }
}

function buildFaceScan(frames,device,challengeId,challenge){
  const ch={id:challengeId, results:[]};
  if(challenge && challenge.nonce){
    ch.nonce=challenge.nonce; ch.action=challenge.action;

    if(challenge.params) ch.params=challenge.params;
  }
  return {
    version:1,
    device:device,
    frames:frames.map(f=>({jpeg_bytes:f.bytes, ts_ms:Math.round(f.ts), pose:null})),
    challenge:ch
  };
}
function bytesToB64(u8){
  let s=''; const CH=0x8000;
  for(let i=0;i<u8.length;i+=CH) s+=String.fromCharCode.apply(null,u8.subarray(i,i+CH));
  return btoa(s);
}
function b64ToBytes(b64){
  const s=atob(b64), u=new Uint8Array(s.length);
  for(let i=0;i<s.length;i++) u[i]=s.charCodeAt(i);
  return u;
}
