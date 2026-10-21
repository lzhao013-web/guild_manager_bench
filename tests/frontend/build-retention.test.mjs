import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdtemp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { build } from "vite";

const root = fileURLToPath(new URL("../../", import.meta.url));
const configFile = join(root, "web/workbench/vite.config.ts");

test("重新构建会更新入口，但保留旧页面依赖的 JS 和 CSS 分包", async t => {
    const directory = await mkdtemp(join(root, "runs/.build-retention-"));
    t.after(() => rm(directory, { recursive: true, force: true }));
    const source = join(directory, "source");
    const outDir = join(directory, "dist");
    await mkdir(source);
    await writeFile(join(source, "index.html"), '<html><body><script type="module" src="/main.js"></script></body></html>');
    await writeFile(join(source, "main.js"), 'window.openPage = () => import("./page.js").then(page => page.label);');
    const compile = () => build({ configFile, root: source, publicDir: false, logLevel: "silent", build: { outDir } });
    await writeFile(join(source, "page.js"), 'import "./page.css"; export const label = "version-one";');
    await writeFile(join(source, "page.css"), 'body { color: rgb(10, 20, 30); }');
    await compile();
    const oldIndex = await readFile(join(outDir, "index.html"), "utf8");
    const oldAssets = new Map(await Promise.all((await readdir(join(outDir, "assets")))
        .map(async name => [name, await readFile(join(outDir, "assets", name))])));
    assert.ok([...oldAssets.keys()].some(name => name.endsWith(".css")));
    assert.ok([...oldAssets.keys()].filter(name => name.endsWith(".js")).length >= 2);
    await writeFile(join(source, "page.js"), 'import "./page.css"; export const label = "version-two";');
    await writeFile(join(source, "page.css"), 'body { color: rgb(40, 50, 60); }');
    await compile();
    assert.notEqual(await readFile(join(outDir, "index.html"), "utf8"), oldIndex, "新入口必须指向本次构建");
    for (const [name, content] of oldAssets) {
        const path = join(outDir, "assets", name);
        assert.ok(existsSync(path), `已打开页面依赖的旧分包被删除：${name}`);
        assert.deepEqual(await readFile(path), content, `旧分包内容被改写：${name}`);
    }
    const added = (await readdir(join(outDir, "assets"))).filter(name => !oldAssets.has(name));
    assert.ok(added.some(name => name.endsWith(".js")), "没有生成新版脚本，测试未覆盖跨版本分包");
    assert.ok(added.some(name => name.endsWith(".css")), "没有生成新版样式，测试未覆盖跨版本分包");
});
