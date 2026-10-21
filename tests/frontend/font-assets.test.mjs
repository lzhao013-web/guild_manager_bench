import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const root = new URL("../../web/workbench/", import.meta.url);
const css = readFileSync(new URL("src/fonts.css", root), "utf8");
const faces = [...css.matchAll(/@font-face\s*\{([^}]+)\}/g)].map(match => match[1]);

test("字体声明仅引用随工作台提供的有效 WOFF2，不依赖外网或本机安装", () => {
    assert.ok(faces.length > 0);
    for (const face of faces) {
        const urls = [...face.matchAll(/url\("([^"]+)"\)/g)].map(match => match[1]);
        assert.equal(urls.length, 1);
        assert.match(urls[0], /^\/workbench\/fonts\/.*\.woff2$/);
        const file = new URL(`public/${urls[0].replace("/workbench/", "")}`, root);
        assert.equal(readFileSync(file).subarray(0, 4).toString(), "wOF2");
        assert.ok(face.includes("font-display: swap;"));
        assert.ok(!face.includes("local("));
    }
});

test("两套字体完整提供使用中的四个字重，MiSans 保留官方分片范围", () => {
    for (const family of ["MiSans", "JetBrains Mono"]) {
        const selected = faces.filter(face => face.match(/font-family:\s*"?([^";]+)"?;/)?.[1] === family);
        assert.deepEqual([...new Set(selected.map(face => Number(face.match(/font-weight:\s*(\d+)/)[1])))].sort(), [400, 500, 600, 700]);
        if (family === "MiSans") for (const face of selected) assert.match(face, /unicode-range:\s*U\+/);
    }
});

test("字体资源同时附带原始许可证及明确的 MiSans 使用说明", () => {
    assert.equal(readFileSync(new URL("public/fonts/misans/LICENSE.pdf", root)).subarray(0, 4).toString(), "%PDF");
    const ofl = readFileSync(new URL("public/fonts/jetbrains-mono/OFL.txt", root), "utf8");
    assert.ok(ofl.includes("Copyright 2020 The JetBrains Mono Project Authors"));
    assert.ok(ofl.includes("SIL OPEN FONT LICENSE Version 1.1"));
    assert.ok(readFileSync(new URL("public/fonts/NOTICE.txt", root), "utf8").includes("本软件使用小米 MiSans 字体"));
});
