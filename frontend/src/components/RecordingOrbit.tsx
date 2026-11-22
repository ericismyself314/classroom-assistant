import { useEffect, useRef } from 'react';
import glass from '../assets/orbit-glass.webp';
import resting from '../assets/orbit-rest.webp';

// The four hoops share a centre. Each vertex belongs to the round bevel of a torus.
function makeRenderer(gl: WebGLRenderingContext) {
  const program = gl.createProgram();
  if (!program) return null;
  const vertex = `
    attribute vec2 uv;
    uniform vec4 shape;
    uniform vec3 movement;
    varying vec3 normal, point;
    varying float angle;
    void main() {
      float a = uv.x, b = uv.y, k = shape.z, lean = shape.w;
      vec3 axis = vec3(cos(k), sin(k), 0.);
      vec3 side = vec3(-sin(k)*cos(lean), cos(k)*cos(lean), sin(lean));
      vec3 plane = cross(axis,side);
      vec3 radial = cos(a)*axis + sin(a)*side;
      vec3 p = (shape.x + shape.y*cos(b))*radial + 1.45*sin(b)*plane;
      vec3 n = normalize(cos(b)*radial/shape.y + sin(b)*plane/1.45);
      float c=cos(movement.x), s=sin(movement.x);
      mat3 yaw=mat3(c,0.,-s,0.,1.,0.,s,0.,c);
      point=yaw*p+vec3(4.,1.,0.);
      normal=yaw*n; angle=a;
      vec2 xy=point.xy*(710./(710.-point.z))-vec2(21.,0.);
      gl_Position=vec4(xy*vec2(1.,-1.)/158.,-point.z/180.,1.);
    }`;
  const fragment = `
    precision highp float;
    varying vec3 normal, point;
    varying float angle;
    uniform vec3 tint, movement;
    uniform float voice;
    void main() {
      vec3 n=normalize(normal), eye=normalize(vec3(0.,0.,710.)-point);
      float front=clamp(dot(n,eye),0.,1.), depth=clamp((point.z+105.)/215.,0.,1.);
      float key=pow(max(0.,dot(n,normalize(eye+vec3(-.53,-.48,.7)))),12.);
      float rim=pow(1.-front,2.5);
      float shine=pow(.5+.5*cos(angle-movement.y),6.);
      float echo=voice*pow(.5+.5*cos(angle-movement.z),11.);
      vec3 color=tint*(.32+.46*depth+.16*rim+.24*shine)+
        vec3(.99,.96,.84)*(.45*key+.11*rim)+
        mix(tint,vec3(1.,.98,.85),.5)*(.16+.39*shine+.80*echo)*(.35+.65*front);
      gl_FragColor=vec4(color,1.);
    }`;
  for (const [type, source] of [[gl.VERTEX_SHADER, vertex], [gl.FRAGMENT_SHADER, fragment]] as const) {
    const shader = gl.createShader(type);
    if (!shader) { gl.deleteProgram(program); return null; }
    gl.shaderSource(shader, source); gl.compileShader(shader);
    gl.attachShader(program, shader); gl.deleteShader(shader);
  }
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) { gl.deleteProgram(program); return null; }
  const coordinates: number[] = [], indices: number[] = [];
  const segments = 112, sides = 14;
  for (let j = 0; j <= sides; j++) {
    for (let i = 0; i <= segments; i++) {
      coordinates.push(2*Math.PI*i/segments, 2*Math.PI*j/sides);
      if (i < segments && j < sides) {
        const a = j*(segments+1)+i, b = a+segments+1;
        indices.push(a,a+1,b+1,a,b+1,b);
      }
    }
  }
  const mesh = gl.createBuffer(), elements = gl.createBuffer();
  gl.useProgram(program);
  gl.bindBuffer(gl.ARRAY_BUFFER,mesh); gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(coordinates),gl.STATIC_DRAW);
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,elements); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,new Uint16Array(indices),gl.STATIC_DRAW);
  const uv = gl.getAttribLocation(program,'uv');
  gl.enableVertexAttribArray(uv); gl.vertexAttribPointer(uv,2,gl.FLOAT,false,0,0);
  gl.enable(gl.DEPTH_TEST); gl.clearColor(0,0,0,0);
  const uniforms = ['shape','movement','tint','voice'].map(name => gl.getUniformLocation(program,name));
  const hoops = [[102,3.15,.56,.82,.69],[91,2.45,.90,.73,.49],[89,2.15,.44,.70,.61],[97,1.7,.80,.83,.69]];
  return {
    draw(phase: number, pulse: number, energy: number) {
      gl.viewport(0,0,gl.drawingBufferWidth,gl.drawingBufferHeight);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      const tilt = [-.51+.47*Math.sin(phase),.68+phase,1.73-phase,2.39+.41*Math.sin(phase+.63)];
      const lean = [.63+.23*Math.sin(phase+.2),1.14+.23*Math.cos(phase+.7),1.23+.19*Math.sin(phase-.4),.92+.30*Math.cos(phase+1.6)];
      gl.uniform1f(uniforms[3],energy);
      hoops.forEach(([r,w,red,green,blue],i) => {
        const sign = i%2 ? -1 : 1;
        gl.uniform4f(uniforms[0],r,w,tilt[i],lean[i]+sign*.08*energy);
        gl.uniform3f(uniforms[1],.21*Math.sin(phase-.4),sign*phase-i*.98,sign*pulse+i*.93);
        gl.uniform3f(uniforms[2],red,green,blue);
        gl.drawElements(gl.TRIANGLES,indices.length,gl.UNSIGNED_SHORT,0);
      });
    },
    dispose() { gl.deleteBuffer(mesh); gl.deleteBuffer(elements); gl.deleteProgram(program); },
  };
}

export function RecordingOrbit({ stream = null, running = false, idle = false, visible = true, actionLabel, onActivate, disabled }: {
  stream?: MediaStream | null; running?: boolean; idle?: boolean; visible?: boolean;
  actionLabel: string; onActivate: () => void; disabled?: boolean;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const motion = useRef({ phase: 0, pulse: 0, level: 0, pressure: 0 });
  useEffect(() => {
    const node = canvas.current;
    if (!node || (!stream && !idle) || !visible) return;
    const host = node.parentElement!;
    const gl = node.getContext('webgl', { alpha: true, antialias: true, depth: true });
    if (!gl) return;
    const renderer = makeRenderer(gl);
    if (!renderer) return;
    const state = motion.current;
    let audio: AudioContext | undefined, analyser: AnalyserNode | undefined;
    if (running && stream) try {
      audio = new AudioContext(); analyser = audio.createAnalyser(); analyser.fftSize = 1024;
      audio.createMediaStreamSource(stream).connect(analyser);
      void audio.resume().catch(() => {});
    } catch { /* The visual can run without a level meter. */ }
    const samples = new Uint8Array(1024);
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    let request = 0, last = 0;
    const draw = (now: number) => {
      const dt = last ? Math.min((now-last)/1000,.05) : 0; last=now;
      if (running && !reduced.matches) {
        let sum = 0;
        analyser?.getByteTimeDomainData(samples);
        if (analyser) for (const byte of samples) sum += ((byte-128)/128)**2;
        const rms = Math.sqrt(sum/samples.length);
        const level = Math.min(1,Math.max(0,(20*Math.log10(Math.max(.0001,rms))+54)/42));
        state.level += (level-state.level)*(-Math.expm1(-dt/(level > state.level ? .14 : .31)));
        state.pressure += (state.level-state.pressure)*(-Math.expm1(-dt/.13));
        state.phase += dt*(.23+.52*state.pressure); state.pulse += dt*(1.45+2.1*state.level);
      } else if (idle && !reduced.matches) {
        state.phase += dt * .17;
        state.pulse += dt * .9;
      }
      const size = Math.round(node.clientWidth*Math.min(window.devicePixelRatio || 1,2));
      if (size && node.width !== size) node.width=node.height=size;
      renderer.draw(state.phase,state.pulse,state.level);
      const swell = idle ? .012 * (1 - Math.cos(state.pulse)) : .145 * state.pressure;
      host.style.transform=`scale(${reduced.matches ? 1 : 1 + swell})`;
      host.dataset.live='true';
      if ((running || idle) && !reduced.matches && !document.hidden) request=requestAnimationFrame(draw);
    };
    const restart = () => { cancelAnimationFrame(request); last=0; if (!document.hidden) draw(performance.now()); };
    const lost = () => { cancelAnimationFrame(request); delete host.dataset.live; };
    node.addEventListener('webglcontextlost',lost);
    document.addEventListener('visibilitychange',restart); reduced.addEventListener('change',restart);
    restart();
    return () => {
      cancelAnimationFrame(request); document.removeEventListener('visibilitychange',restart);
      reduced.removeEventListener('change',restart); node.removeEventListener('webglcontextlost',lost);
      delete host.dataset.live; renderer.dispose(); if (audio) void audio.close().catch(()=>{});
    };
  },[stream,running,idle,visible]);
  return <div className="orbit-switch">
    <div className="record-orbit" data-idle={idle && visible || undefined} aria-hidden="true"><img src={resting} alt="" /><img className="orbit-shell" src={glass} alt="" />
      {(stream || idle) && <canvas ref={canvas} />}</div>
    <button type="button" aria-label={`录音球：${actionLabel}`} title={actionLabel} disabled={disabled} onClick={onActivate} />
  </div>;
}


