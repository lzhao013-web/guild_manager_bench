import { describe, expect, it } from "vitest";
import { expandDraft, fmt, modelEntries, safeTemplate } from "./model";
import { defaultDraft } from "./NewBatch";
import type { EventRecord } from "./types";

describe("实验参数契约", () => {
    it("展开模型、强度、种子和重复次数，并且不悄悄更换种子", () => {
        const draft = {
            ...defaultDraft("default"),
            efforts: "medium,high",
            seeds: "0,42",
            repeats: 2,
        };
        draft.rows.push({
            provider: "openai",
            model: "other",
            base_url: "",
            api_key: "",
        });
        const runs = expandDraft(draft);
        expect(runs).toHaveLength(16);
        expect(runs.filter((r) => r.game_seed === 0)).toHaveLength(8);
        expect(runs.filter((r) => r.game_seed === 42)).toHaveLength(8);
    });
    it("默认种子不被当成零，零分不被当成未知", () => {
        expect(expandDraft(defaultDraft("default"))[0].game_seed).toBeNull();
        expect(fmt(0)).toBe("0");
        expect(fmt(null)).toBe("未知");
    });
    it("拒绝错误种子、不支持的强度和过多组合", () => {
        expect(() =>
            expandDraft({ ...defaultDraft("default"), seeds: "42,wrong" }),
        ).toThrow("整数");
        const draft = defaultDraft("default");
        draft.rows[0].provider = "google";
        draft.efforts = "xhigh";
        expect(() => expandDraft(draft)).toThrow("不支持");
        expect(() =>
            expandDraft({
                ...defaultDraft("default"),
                efforts: "high,low",
                repeats: 64,
            }),
        ).toThrow("64");
    });
    it("凭证可以随启动请求发送，但不能写进模板，包括 URL 夹带的凭证", () => {
        const draft = defaultDraft("full");
        draft.rows[0].api_key = "private-value";
        expect(expandDraft(draft)[0].api_key).toBe("private-value");
        expect(JSON.stringify(safeTemplate(draft))).not.toContain(
            "private-value",
        );
        draft.rows[0].base_url = "https://example.test/v1?key=private-value";
        expect(() => safeTemplate(draft)).toThrow("认证信息");
    });
    it("同一存档不能批量复制续跑", () => {
        expect(() =>
            expandDraft({
                ...defaultDraft("default"),
                resume_run_id: "existing",
                repeats: 2,
            }),
        ).toThrow("一个续跑");
    });
});

it("流式正文与完整响应不重复，推理只来自实际返回字段", () => {
    const events = [
        { type: "model_request", turn: 1, step: 1, request: { messages: [] } },
        { type: "model_delta", text: "实际" },
        { type: "model_delta", text: "正文" },
        { type: "model_reasoning_delta", text: "接口提供的摘要" },
        {
            type: "model_response",
            text: "实际正文",
            usage: { input_tokens: 0 },
        },
        { type: "model_request", turn: 1, step: 2 },
        {
            type: "model_response",
            text: "",
            tool_calls: [{ name: "end_turn" }],
        },
    ].map((event, i) => ({
        event,
        cursor: i + 1,
        at: "2026-10-18T00:00:00Z",
    })) as EventRecord[];
    const entries = modelEntries(events);
    expect(entries).toHaveLength(2);
    expect(entries[0].text).toBe("实际正文");
    expect(entries[0].reasoning).toBe("接口提供的摘要");
    expect(entries[1].reasoning).toBe("");
});
