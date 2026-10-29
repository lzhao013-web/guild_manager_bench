import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.restoreAllMocks();
});
// jsdom 不提供几何布局。单元测试只补原生接口，尺寸与溢出由浏览器验收覆盖。
Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value() {} });
Object.defineProperty(globalThis, "ResizeObserver", { configurable: true, writable: true, value: class {
    observe() {}
    unobserve() {}
    disconnect() {}
} });
Object.defineProperty(window, "matchMedia", {
    value: vi.fn().mockImplementation((query) => ({
        matches: false,
        media: query,
        addEventListener() {},
        removeEventListener() {},
    })),
});
