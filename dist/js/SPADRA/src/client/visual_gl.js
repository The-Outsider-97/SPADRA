/** The WebGL parts the 3D view and the snapshot renderer share: shader programs, the sun's shadow map and thick edge lines. Each owns its GPU objects and
 *  frees them in ``dispose``, so neither the view nor a snapshot leaks buffers, textures or framebuffers. */
import { lookAt, multiply, ortho } from "./math3d.js";
import { DEPTH_FS, DEPTH_VS, EDGE_FS, EDGE_VS, fitLight } from "./visual_shading.js";
export class GlUnavailable extends Error {
    constructor(message) { super(message); this.name = "GlUnavailable"; }
}
export function compileProgram(gl, vs, fs, what = "The") {
    const make = (type, src) => {
        const sh = gl.createShader(type);
        if (!sh)
            throw new GlUnavailable("This browser could not create a shader.");
        gl.shaderSource(sh, src);
        gl.compileShader(sh);
        if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
            const log = gl.getShaderInfoLog(sh) ?? "unknown error";
            gl.deleteShader(sh);
            throw new GlUnavailable(`${what} shader did not compile: ${log}`);
        }
        return sh;
    };
    const v = make(gl.VERTEX_SHADER, vs), f = make(gl.FRAGMENT_SHADER, fs);
    const prog = gl.createProgram();
    if (!prog)
        throw new GlUnavailable("This browser could not create a shader program.");
    gl.attachShader(prog, v);
    gl.attachShader(prog, f);
    gl.linkProgram(prog);
    gl.deleteShader(v);
    gl.deleteShader(f); // the program keeps what it linked
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
        const log = gl.getProgramInfoLog(prog) ?? "unknown error";
        gl.deleteProgram(prog);
        throw new GlUnavailable(`${what} shader did not link: ${log}`);
    }
    return { prog, u: new Map(), a: new Map() };
}
export function uniform(gl, p, name) {
    if (!p.u.has(name))
        p.u.set(name, gl.getUniformLocation(p.prog, name));
    return p.u.get(name) ?? null;
}
export function attribute(gl, p, name) {
    if (!p.a.has(name))
        p.a.set(name, gl.getAttribLocation(p.prog, name));
    return p.a.get(name) ?? -1;
}
/** The sun's shadow map: a depth texture the model is drawn into from the sun, fitted around the model. The interactive view draws it again only when the
 *  model, the sun or the size changes (``key``); a snapshot draws it once. */
export class ShadowPass {
    gl;
    size;
    texture;
    lightVp = new Float32Array(16);
    fbo;
    program;
    key = "";
    constructor(gl, size) {
        this.gl = gl;
        this.size = Math.max(256, Math.min(size, gl.getParameter(gl.MAX_TEXTURE_SIZE)));
        const tex = gl.createTexture(), fbo = gl.createFramebuffer();
        if (!tex || !fbo) {
            if (tex)
                gl.deleteTexture(tex);
            if (fbo)
                gl.deleteFramebuffer(fbo);
            throw new GlUnavailable("This browser ran out of graphics memory.");
        }
        this.texture = tex;
        this.fbo = fbo;
        const previous = gl.getParameter(gl.TEXTURE_BINDING_2D);
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.DEPTH_COMPONENT24, this.size, this.size, 0, gl.DEPTH_COMPONENT, gl.UNSIGNED_INT, null);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_COMPARE_MODE, gl.COMPARE_REF_TO_TEXTURE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_COMPARE_FUNC, gl.LEQUAL);
        const previousFbo = gl.getParameter(gl.FRAMEBUFFER_BINDING);
        gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.TEXTURE_2D, tex, 0);
        gl.drawBuffers([gl.NONE]);
        gl.readBuffer(gl.NONE);
        const ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
        gl.bindFramebuffer(gl.FRAMEBUFFER, previousFbo);
        gl.bindTexture(gl.TEXTURE_2D, previous);
        if (!ok) {
            this.dispose();
            throw new GlUnavailable("This graphics card cannot draw shadows; switch shadows off.");
        }
        this.program = compileProgram(gl, DEPTH_VS, DEPTH_FS, "The shadow");
    }
    /** Draw ``meshes`` from the sun; true when it did draw. ``key`` names what the picture depends on (the geometry and the sun); the same key is not drawn twice. */
    render(bounds, sun, meshes, key) {
        const gl = this.gl;
        const fit = fitLight(bounds, sun);
        this.lightVp = multiply(ortho(fit.half, fit.half, fit.near, fit.far), lookAt(fit.eye, fit.target, fit.up));
        if (key === this.key)
            return false;
        const saved = { fbo: gl.getParameter(gl.FRAMEBUFFER_BINDING), viewport: gl.getParameter(gl.VIEWPORT),
            program: gl.getParameter(gl.CURRENT_PROGRAM), depthTest: gl.isEnabled(gl.DEPTH_TEST), blend: gl.isEnabled(gl.BLEND), cull: gl.isEnabled(gl.CULL_FACE),
            offset: gl.isEnabled(gl.POLYGON_OFFSET_FILL), depthMask: gl.getParameter(gl.DEPTH_WRITEMASK), colorMask: gl.getParameter(gl.COLOR_WRITEMASK) };
        gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo);
        gl.viewport(0, 0, this.size, this.size);
        gl.enable(gl.DEPTH_TEST);
        gl.depthMask(true);
        gl.disable(gl.BLEND);
        gl.disable(gl.CULL_FACE);
        gl.disable(gl.POLYGON_OFFSET_FILL);
        gl.clearDepth(1);
        gl.clear(gl.DEPTH_BUFFER_BIT);
        gl.colorMask(false, false, false, false);
        gl.useProgram(this.program.prog);
        gl.uniformMatrix4fv(uniform(gl, this.program, "u_vp"), false, this.lightVp);
        const aPos = attribute(gl, this.program, "a_pos");
        gl.enableVertexAttribArray(aPos);
        for (const m of meshes) {
            gl.bindBuffer(gl.ARRAY_BUFFER, m.buf);
            gl.vertexAttribPointer(aPos, 3, gl.FLOAT, false, 24, 0);
            gl.drawArrays(gl.TRIANGLES, 0, m.count);
        }
        gl.disableVertexAttribArray(aPos);
        gl.colorMask(saved.colorMask[0] ?? true, saved.colorMask[1] ?? true, saved.colorMask[2] ?? true, saved.colorMask[3] ?? true);
        gl.depthMask(saved.depthMask);
        if (!saved.depthTest)
            gl.disable(gl.DEPTH_TEST);
        if (saved.blend)
            gl.enable(gl.BLEND);
        if (saved.cull)
            gl.enable(gl.CULL_FACE);
        if (saved.offset)
            gl.enable(gl.POLYGON_OFFSET_FILL);
        gl.bindFramebuffer(gl.FRAMEBUFFER, saved.fbo);
        gl.viewport(saved.viewport[0] ?? 0, saved.viewport[1] ?? 0, saved.viewport[2] ?? 1, saved.viewport[3] ?? 1);
        gl.useProgram(saved.program);
        this.key = key;
        return true;
    }
    /** Forget the drawn picture (a context change or a model change that the key cannot see). */
    invalidate() { this.key = ""; }
    dispose() {
        const gl = this.gl;
        gl.deleteTexture(this.texture);
        gl.deleteFramebuffer(this.fbo);
        if (this.program)
            gl.deleteProgram(this.program.prog);
    }
}
/** A depth texture with comparison on it, one texel big: a shadow sampler must always have one bound, even when shadows are off and it is never read. */
export function placeholderShadowTexture(gl) {
    const t = gl.createTexture();
    if (!t)
        throw new GlUnavailable("This browser ran out of graphics memory.");
    const previous = gl.getParameter(gl.TEXTURE_BINDING_2D);
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.DEPTH_COMPONENT24, 1, 1, 0, gl.DEPTH_COMPONENT, gl.UNSIGNED_INT, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_COMPARE_MODE, gl.COMPARE_REF_TO_TEXTURE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_COMPARE_FUNC, gl.LEQUAL);
    gl.bindTexture(gl.TEXTURE_2D, previous);
    return t;
}
/** Edges as quads of a chosen width in pixels. A buffer holds segments as two vec3 endpoints (24 bytes a segment). */
/** The slope factor for ``gl.polygonOffset`` while the faces are drawn: the faces are pushed back by their own depth slope times this many pixels, which is how far a line of
 *  ``widthPx`` reaches over the faces beside its edge. Without it a thick line is cut off on the side where the neighbouring face is nearer to the eye than the edge. */
export const edgeOffsetFactor = (widthPx) => 1 + Math.max(0, widthPx) / 2;
export class EdgeRenderer {
    gl;
    program;
    constructor(gl) {
        this.gl = gl;
        this.program = compileProgram(gl, EDGE_VS, EDGE_FS, "The edge");
    }
    /** Draw ``count`` segments from ``buf``. ``viewport`` is the render target's size in pixels and ``widthPx`` the line width in those pixels. */
    draw(buf, count, vp, viewport, widthPx, colour, depthBias = 0) {
        if (count <= 0 || widthPx <= 0)
            return;
        const gl = this.gl, p = this.program;
        const saved = { program: gl.getParameter(gl.CURRENT_PROGRAM), offset: gl.isEnabled(gl.POLYGON_OFFSET_FILL), cull: gl.isEnabled(gl.CULL_FACE) };
        gl.useProgram(p.prog);
        gl.disable(gl.POLYGON_OFFSET_FILL);
        gl.disable(gl.CULL_FACE); // faces are pushed back by the offset; the edges are not, so they win where they meet
        gl.uniformMatrix4fv(uniform(gl, p, "u_vp"), false, vp);
        gl.uniform2f(uniform(gl, p, "u_viewport"), viewport[0], viewport[1]);
        gl.uniform1f(uniform(gl, p, "u_width"), widthPx);
        gl.uniform1f(uniform(gl, p, "u_depthBias"), depthBias);
        gl.uniform4f(uniform(gl, p, "u_color"), colour[0], colour[1], colour[2], colour[3]);
        const a = attribute(gl, p, "a_a"), b = attribute(gl, p, "a_b");
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.enableVertexAttribArray(a);
        gl.vertexAttribPointer(a, 3, gl.FLOAT, false, 24, 0);
        gl.vertexAttribDivisor(a, 1);
        gl.enableVertexAttribArray(b);
        gl.vertexAttribPointer(b, 3, gl.FLOAT, false, 24, 12);
        gl.vertexAttribDivisor(b, 1);
        gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, count);
        gl.vertexAttribDivisor(a, 0);
        gl.vertexAttribDivisor(b, 0);
        gl.disableVertexAttribArray(a);
        gl.disableVertexAttribArray(b);
        if (saved.offset)
            gl.enable(gl.POLYGON_OFFSET_FILL);
        if (saved.cull)
            gl.enable(gl.CULL_FACE);
        gl.useProgram(saved.program);
    }
    dispose() { this.gl.deleteProgram(this.program.prog); }
}
