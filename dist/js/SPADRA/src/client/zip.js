/** A minimal ZIP writer (entries stored, not compressed: PNG frames are already compressed). Pure, so it is tested against a real unzip. */
const TABLE = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
        let c = n;
        for (let k = 0; k < 8; k++)
            c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
        t[n] = c >>> 0;
    }
    return t;
})();
export function crc32(data) {
    let c = 0xffffffff;
    for (let i = 0; i < data.length; i++)
        c = (TABLE[(c ^ (data[i] ?? 0)) & 0xff] ?? 0) ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
}
/** ``date`` fixes the timestamps so the same input gives the same bytes. */
export function zipStore(entries, date = new Date()) {
    const enc = new TextEncoder();
    const dosTime = ((date.getHours() << 11) | (date.getMinutes() << 5) | (date.getSeconds() >> 1)) & 0xffff;
    const dosDate = (((Math.max(1980, date.getFullYear()) - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate()) & 0xffff;
    if (entries.length > 0xffff)
        throw new Error("A ZIP of this kind holds at most 65535 files");
    const locals = [];
    const centrals = [];
    let offset = 0;
    for (const e of entries) {
        const name = enc.encode(e.name);
        if (e.data.length >= 0xffffffff)
            throw new Error("A file in the ZIP is larger than 4 GB");
        const crc = crc32(e.data);
        const local = new DataView(new ArrayBuffer(30));
        local.setUint32(0, 0x04034b50, true);
        local.setUint16(4, 20, true);
        local.setUint16(6, 0x0800, true); // bit 11: UTF-8 names
        local.setUint16(8, 0, true);
        local.setUint16(10, dosTime, true);
        local.setUint16(12, dosDate, true);
        local.setUint32(14, crc, true);
        local.setUint32(18, e.data.length, true);
        local.setUint32(22, e.data.length, true);
        local.setUint16(26, name.length, true);
        local.setUint16(28, 0, true);
        const central = new DataView(new ArrayBuffer(46));
        central.setUint32(0, 0x02014b50, true);
        central.setUint16(4, 20, true);
        central.setUint16(6, 20, true);
        central.setUint16(8, 0x0800, true);
        central.setUint16(10, 0, true);
        central.setUint16(12, dosTime, true);
        central.setUint16(14, dosDate, true);
        central.setUint32(16, crc, true);
        central.setUint32(20, e.data.length, true);
        central.setUint32(24, e.data.length, true);
        central.setUint16(28, name.length, true);
        central.setUint32(42, offset, true);
        locals.push(new Uint8Array(local.buffer), name, e.data);
        centrals.push(new Uint8Array(central.buffer), name);
        offset += 30 + name.length + e.data.length;
    }
    const centralSize = centrals.reduce((n, p) => n + p.length, 0);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true);
    end.setUint16(8, entries.length, true);
    end.setUint16(10, entries.length, true);
    end.setUint32(12, centralSize, true);
    end.setUint32(16, offset, true);
    const parts = [...locals, ...centrals, new Uint8Array(end.buffer)];
    const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
    let at = 0;
    for (const p of parts) {
        out.set(p, at);
        at += p.length;
    }
    return out;
}
