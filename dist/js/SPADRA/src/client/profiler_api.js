export class ProfilerApi {
    api;
    constructor(api) {
        this.api = api;
    }
    plugins() { return this.api.get("/plugins"); }
    info() { return this.api.get("/profiler"); }
    create(body) { return this.api.post("/profiler/new", body); }
    openUpload(file) { return this.api.upload(`/profiler/import?name=${encodeURIComponent(file.name)}`, file); }
    open(body) { return this.api.post("/profiler/open", body); }
    state(sid) { return this.api.get(`/profiler/${sid}`); }
    op(sid, op, args = {}, view) {
        return this.api.post(`/profiler/${sid}/op`, { op, args, ...(view === undefined ? {} : { view }) });
    }
    undo(sid) { return this.api.post(`/profiler/${sid}/undo`); }
    redo(sid) { return this.api.post(`/profiler/${sid}/redo`); }
    repeat(sid) { return this.api.post(`/profiler/${sid}/repeat`, {}); }
    save(sid, body) { return this.api.post(`/profiler/${sid}/save`, body); }
    autosave(sid) { return this.api.post(`/profiler/${sid}/autosave`); }
    importFile(sid, body) { return this.api.post(`/profiler/${sid}/import`, body); }
    importUpload(sid, file, insert) { return this.api.upload(`/profiler/${sid}/import_upload?name=${encodeURIComponent(file.name)}&insert=${insert ? 1 : 0}`, file); }
    exportFile(sid, body) { return this.api.post(`/profiler/${sid}/export`, body); }
    report(sid, body) { return this.api.post(`/profiler/${sid}/report`, body); }
    check(sid, purpose, extra = {}) { return this.api.post(`/profiler/${sid}/check`, { purpose, ...extra }); }
    send(sid, body) {
        return this.api.post(`/profiler/${sid}/send`, body);
    }
    close(sid, discard) { return this.api.post(`/profiler/${sid}/close`, { discard }); }
}
