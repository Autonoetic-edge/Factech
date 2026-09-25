// The approved 3D head guide (design study 04), ported from the prototype unchanged in
// shape and shading. It is decorative: every instruction is also visible text.
// The pose is set by the page from SDK cues or practice buttons, never by a timer.

const ANGLE = { forward: 0, left: -0.20, right: 0.20 }; // ~11 deg, the small turn the engine needs; mirrored guide: participant-left is screen-left

function headPoint(u, v) {
  const y = Math.cos(v), ring = Math.sin(v), jaw = 1 - 0.22 * Math.max(0, -y), x = Math.sin(u) * ring * 0.70 * jaw;
  let z = Math.cos(u) * ring * 0.65;
  const g = (a, s) => Math.exp(-((a / s) ** 2)), front = Math.pow(Math.max(0, Math.cos(u)), 8);
  // Broad, quiet sculptural features; deliberately no drawn eyes or mouth.
  z += front * (0.085 * g(x, 0.10) * g(y - 0.06, 0.29) + 0.16 * g(x, 0.13) * g(y + 0.12, 0.13)
    - 0.035 * g(Math.abs(x) - 0.265, 0.115) * g(y - 0.17, 0.085)
    + 0.027 * g(Math.abs(x) - 0.27, 0.17) * g(y - 0.30, 0.075)
    - 0.025 * g(x, 0.20) * g(y + 0.43, 0.025) + 0.020 * g(x, 0.20) * g(y + 0.39, 0.045)
    + 0.025 * g(x, 0.20) * g(y + 0.48, 0.045) + 0.048 * g(x, 0.28) * g(y + 0.68, 0.17));
  return [x, y * 1.04 + 0.22, z];
}

const VERTEX = `attribute vec3 position; attribute vec3 normal;
uniform float yaw; uniform vec2 aspect; varying vec3 N; varying vec3 P;
void main(){float c=cos(yaw),s=sin(yaw);mat3 rot=mat3(c,0.,-s,0.,1.,0.,s,0.,c);
vec3 p=rot*position;N=rot*normal;P=p;float f=4.8/(4.8-p.z);
gl_Position=vec4(p.x*aspect.x*f,p.y*aspect.y*f+.025,-p.z*.15,1.);}`;
const FRAGMENT = `precision mediump float; varying vec3 N; varying vec3 P;
void main(){vec3 n=normalize(N);vec3 key=normalize(vec3(-.65,.95,1.4));
float diffuse=max(0.,dot(n,key));float fill=max(0.,dot(n,normalize(vec3(.8,.15,.4))));
float rim=pow(1.-max(n.z,0.),3.)*.16;
float spec=pow(max(0.,dot(n,normalize(key+vec3(0.,0.,1.)))),35.)*.035;
vec3 shade=vec3(.18,.17,.15);vec3 clay=vec3(.77,.74,.68);
vec3 color=mix(shade,clay,.29+.69*diffuse)+fill*vec3(.045,.042,.035)+rim*vec3(.26,.24,.20)+spec;
gl_FragColor=vec4(color,1.);}`;

function createSculpture(canvas) {
  const gl = canvas.getContext('webgl', { alpha: true, antialias: true, premultipliedAlpha: false });
  if (!gl) return null;
  const shader = (type, source) => {
    const s = gl.createShader(type); gl.shaderSource(s, source); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error('shader');
    return s;
  };
  const program = gl.createProgram();
  gl.attachShader(program, shader(gl.VERTEX_SHADER, VERTEX));
  gl.attachShader(program, shader(gl.FRAGMENT_SHADER, FRAGMENT));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('link');
  gl.useProgram(program);

  const vertices = [], normals = [];
  const normalAt = (fn, u, v) => {
    const a = fn(u + 0.001, v), b = fn(u - 0.001, v), c = fn(u, v + 0.001), d = fn(u, v - 0.001);
    const U = a.map((x, i) => x - b[i]), V = c.map((x, i) => x - d[i]);
    const n = [U[1] * V[2] - U[2] * V[1], U[2] * V[0] - U[0] * V[2], U[0] * V[1] - U[1] * V[0]];
    const len = Math.hypot(...n) || 1;
    return n.map((x) => -x / len);
  };
  const surface = (fn, rows, cols) => {
    const pts = [], ns = [];
    for (let j = 0; j <= rows; j++) {
      pts[j] = []; ns[j] = [];
      for (let i = 0; i <= cols; i++) {
        const u = -Math.PI + 2 * Math.PI * i / cols, v = 0.001 + (Math.PI - 0.002) * j / rows;
        pts[j][i] = fn(u, v); ns[j][i] = normalAt(fn, u, v);
      }
    }
    for (let j = 0; j < rows; j++) for (let i = 0; i < cols; i++) {
      for (const [r, c] of [[j, i], [j + 1, i], [j, i + 1], [j, i + 1], [j + 1, i], [j + 1, i + 1]]) {
        vertices.push(...pts[r][c]); normals.push(...ns[r][c]);
      }
    }
  };
  surface(headPoint, 80, 112);
  // Ears, neck and a gently rounded shoulder base share the matte material.
  for (const side of [-1, 1]) surface((u, v) => [side * 0.665 + 0.082 * Math.sin(v) * Math.sin(u), 0.23 + 0.19 * Math.cos(v), -0.02 + 0.13 * Math.sin(v) * Math.cos(u)], 22, 30);
  surface((u, v) => [0.275 * Math.sin(v) * Math.sin(u), -0.83 + 0.48 * Math.cos(v), -0.12 + 0.28 * Math.sin(v) * Math.cos(u)], 28, 40);
  surface((u, v) => [1.04 * Math.sin(v) * Math.sin(u), -1.27 + 0.38 * Math.cos(v), -0.14 + 0.43 * Math.sin(v) * Math.cos(u)], 32, 60);

  const attribute = (name, data) => {
    const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
    const location = gl.getAttribLocation(program, name);
    gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location, 3, gl.FLOAT, false, 0, 0);
  };
  attribute('position', vertices); attribute('normal', normals);
  const yawUniform = gl.getUniformLocation(program, 'yaw'), aspectUniform = gl.getUniformLocation(program, 'aspect');
  gl.enable(gl.DEPTH_TEST); gl.clearColor(0, 0, 0, 0);
  return {
    draw(yaw, w, h, dpr) {
      const scale = Math.min(w * 0.28, h * 0.305);
      gl.viewport(0, 0, Math.round(w * dpr), Math.round(h * dpr));
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.uniform1f(yawUniform, yaw); gl.uniform2f(aspectUniform, 2 * scale / w, 2 * scale / h);
      gl.drawArrays(gl.TRIANGLES, 0, vertices.length / 3);
    },
  };
}

export function createHeadGuide(canvas) {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let sculpture = null;
  try { sculpture = createSculpture(canvas); } catch { /* text instructions remain */ }
  if (!sculpture) canvas.hidden = true;

  let target = 0, angle = 0.18, frame = 0, last = 0, paused = reduced.matches;
  const still = () => paused || reduced.matches;

  function draw() {
    if (!sculpture) return;
    const rect = canvas.parentElement.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dpr = Math.min(devicePixelRatio || 1, 1.75), w = rect.width, h = rect.height;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    }
    angle = still() ? target : angle + (target - angle) * 0.075;
    sculpture.draw(angle, w, h, dpr);
  }
  function loop(now) {
    if (now - last > 32) { draw(); last = now; }
    frame = !still() && !document.hidden ? requestAnimationFrame(loop) : 0;
  }
  function restart() {
    cancelAnimationFrame(frame); frame = 0; draw();
    if (!still() && !document.hidden) frame = requestAnimationFrame(loop);
  }

  const onVisibility = () => (document.hidden ? (cancelAnimationFrame(frame), frame = 0) : restart());
  const onMotion = () => { paused = reduced.matches; restart(); };
  const resize = new ResizeObserver(draw);
  resize.observe(canvas.parentElement);
  document.addEventListener('visibilitychange', onVisibility);
  reduced.addEventListener('change', onMotion);
  restart();

  return {
    available: !!sculpture,
    get paused() { return paused; },
    get reducedMotion() { return reduced.matches; },
    setPose(pose) { target = ANGLE[pose] ?? 0; draw(); },
    setPaused(value) { paused = value; restart(); },
    destroy() {
      cancelAnimationFrame(frame); resize.disconnect();
      document.removeEventListener('visibilitychange', onVisibility);
      reduced.removeEventListener('change', onMotion);
    },
  };
}
