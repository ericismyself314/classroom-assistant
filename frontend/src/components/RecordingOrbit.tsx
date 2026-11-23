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
    uniform vec2 movement;
    varying vec3 normal, point;
    varying float angle, bevel;
    void main() {
      float a = uv.x, b = uv.y, k = shape.z, lean = shape.w;
      vec3 axis = vec3(cos(k), sin(k), 0.);
      vec3 side = vec3(-sin(k)*cos(lean), cos(k)*cos(lean), sin(lean));
      vec3 plane = cross(axis,side);
      vec3 radial = cos(a)*axis + sin(a)*side;
      float thickness = .50 + .19*shape.y;
      vec3 p = (shape.x + shape.y*cos(b))*radial + thickness*sin(b)*plane;
      vec3 n = normalize(cos(b)*radial/shape.y + sin(b)*plane/thickness);
      float c=cos(movement.x), s=sin(movement.x);
      mat3 yaw=mat3(c,0.,-s,0.,1.,0.,s,0.,c);
      point=yaw*p+vec3(4.,1.,0.);
      normal=yaw*n; angle=a; bevel=cos(b);
      vec2 xy=point.xy*(710./(710.-point.z))-vec2(21.,0.);
      gl_Position=vec4(xy*vec2(1.,-1.)/158.,-point.z/180.,1.);
    }`;
  const fragment = `
    precision highp float;
    varying vec3 normal, point;
    varying float angle, bevel;
    uniform vec3 tint;
    uniform vec2 movement;
    uniform float voice;
    void main() {
      vec3 n=normalize(normal), eye=normalize(vec3(0.,0.,710.)-point);
      float depth=smoothstep(-88.,80.,point.z);
      // Reflect a fixed studio environment. The broad cards shape the face;
      // the narrow strip catches the bevel as each hoop turns through the light.
      vec3 ray=normalize(reflect(-eye,n)+point/240.);
      float lip=smoothstep(.54,.90,abs(bevel));
      float sweep=dot(ray,normalize(vec3(-.38,.86,.34)));
      float card=smoothstep(-.62,-.26,sweep);
      float darkBand=smoothstep(.00,.19,sweep)*(1.-smoothstep(.29,.55,sweep));
      float whiteStrip=1.-smoothstep(.025,.16,abs(sweep-.63));
      float warmCard=smoothstep(.52,.94,dot(ray,normalize(vec3(.71,-.48,.39))));
      vec3 reflection=mix(vec3(.45,.49,.52),vec3(.87,.91,.94),card);
      reflection=mix(reflection,vec3(.18,.21,.24),darkBand*.90);
      reflection=mix(reflection,vec3(.94,.80,.64),warmCard*.66);
      reflection=mix(reflection,vec3(.99,.99,.98),whiteStrip);
      vec3 color=reflection*mix(vec3(1.),tint,.24+.54*lip);
      // Light on the front arc gains a little intensity with the voice.
      float echo=voice*pow(.5+.5*cos(angle-movement.y),18.);
      color=mix(color,vec3(1.,.98,.94),.27*echo*(.3+.7*whiteStrip));
      float opacity=mix(.63,1.,depth);
      gl_FragColor=vec4(color*opacity,opacity);
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
  const hoops = [[101,4.4,.72,.90,.86],[91,2.9,.99,.85,.67],[82,2.1,.78,.85,.92],[96,1.25,.95,.95,.94]];
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
        gl.uniform2f(uniforms[1],.21*Math.sin(phase-.4),sign*pulse+i*.93);
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
      // Supersample the fine bevels on standard-density desktop displays.
      const size = Math.round(node.clientWidth*Math.min(Math.max(window.devicePixelRatio || 1,1.5),2));
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


