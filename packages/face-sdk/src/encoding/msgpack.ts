export type MsgpackValue =
  | null | undefined | boolean | number | string | Uint8Array
  | readonly MsgpackValue[]
  | { readonly [key: string]: MsgpackValue };

const MAX_DEPTH = 32;
const utf8 = new TextEncoder();

export function mpEncode(value: MsgpackValue): Uint8Array {
  const parts: Uint8Array[] = [];
  enc(value, parts, 0, new Set<object>());
  let n = 0;
  for (const p of parts) n += p.length;
  const out = new Uint8Array(n);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

const head = (parts: Uint8Array[], ...bytes: number[]): void => { parts.push(Uint8Array.from(bytes)); };
const u16 = (n: number): number[] => [n >> 8 & 255, n & 255];
const u32 = (n: number): number[] => [n >>> 24 & 255, n >>> 16 & 255, n >>> 8 & 255, n & 255];

function enc(v: unknown, parts: Uint8Array[], depth: number, open: Set<object>): void {
  if (depth > MAX_DEPTH) throw new TypeError('msgpack: nesting deeper than ' + MAX_DEPTH);
  if (v === null || v === undefined) return head(parts, 0xc0);
  if (typeof v === 'boolean') return head(parts, v ? 0xc3 : 0xc2);
  if (typeof v === 'number') return number(v, parts);
  if (typeof v === 'string') return str(v, parts);
  if (v instanceof Uint8Array) return bin(v, parts);
  if (typeof v !== 'object') throw new TypeError('msgpack: cannot encode ' + typeof v);
  if (open.has(v)) throw new TypeError('msgpack: cyclic value');
  open.add(v);
  if (Array.isArray(v)) {
    if (v.length <= 15) head(parts, 0x90 | v.length);
    else if (v.length <= 0xffff) head(parts, 0xdc, ...u16(v.length));
    else head(parts, 0xdd, ...u32(v.length));
    for (const x of v) enc(x, parts, depth + 1, open);
  } else {
    const proto = Object.getPrototypeOf(v) as object | null;
    if (proto !== Object.prototype && proto !== null) throw new TypeError('msgpack: cannot encode a non-plain object');
    const map = v as Record<string, unknown>;
    const keys = Object.keys(map);
    if (keys.length <= 15) head(parts, 0x80 | keys.length);
    else if (keys.length <= 0xffff) head(parts, 0xde, ...u16(keys.length));
    else head(parts, 0xdf, ...u32(keys.length));
    for (const k of keys) { str(k, parts); enc(map[k], parts, depth + 1, open); }
  }
  open.delete(v);
}

function number(v: number, parts: Uint8Array[]): void {
  if (!Number.isSafeInteger(v)) {
    const b = new Uint8Array(9);
    b[0] = 0xcb;
    new DataView(b.buffer).setFloat64(1, v);
    parts.push(b);
    return;
  }
  if (v >= 0) {
    if (v <= 0x7f) head(parts, v);
    else if (v <= 0xff) head(parts, 0xcc, v);
    else if (v <= 0xffff) head(parts, 0xcd, ...u16(v));
    else if (v <= 0xffffffff) head(parts, 0xce, ...u32(v));
    else head(parts, 0xcf, ...u32(Math.floor(v / 4294967296)), ...u32(v));
  } else if (v >= -32) head(parts, v + 256);
  else if (v >= -128) head(parts, 0xd0, v + 256);
  else if (v >= -32768) head(parts, 0xd1, ...u16(v));
  else if (v >= -2147483648) head(parts, 0xd2, ...u32(v));
  else {
    const b = new Uint8Array(9);
    b[0] = 0xd3;
    new DataView(b.buffer).setBigInt64(1, BigInt(v));
    parts.push(b);
  }
}

function str(v: string, parts: Uint8Array[]): void {
  const b = utf8.encode(v), n = b.length;
  if (n <= 31) head(parts, 0xa0 | n);
  else if (n <= 0xff) head(parts, 0xd9, n);
  else if (n <= 0xffff) head(parts, 0xda, ...u16(n));
  else head(parts, 0xdb, ...u32(n));
  parts.push(b);
}

function bin(v: Uint8Array, parts: Uint8Array[]): void {
  const n = v.length;
  if (n <= 0xff) head(parts, 0xc4, n);
  else if (n <= 0xffff) head(parts, 0xc5, ...u16(n));
  else head(parts, 0xc6, ...u32(n));
  parts.push(v);
}

export function bytesToB64(u8: Uint8Array): string {
  let s = '';
  const CH = 0x8000;
  for (let i = 0; i < u8.length; i += CH) {
    s += String.fromCharCode.apply(null, u8.subarray(i, i + CH) as unknown as number[]);
  }
  return btoa(s);
}

export function b64ToBytes(b64: string): Uint8Array {
  const s = atob(b64), u = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i);
  return u;
}
