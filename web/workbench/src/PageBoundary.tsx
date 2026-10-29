import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { Button } from "@radix-ui/themes";
import { ArrowsClockwise, WarningCircle } from "@phosphor-icons/react";
import "./page-boundary.css";

export class PageBoundary extends Component<{ name: string; children: ReactNode }, { failed: boolean }> {
    state = { failed: false };

    static getDerivedStateFromError() {
        return { failed: true };
    }

    componentDidCatch(error: Error, info: ErrorInfo) {
        console.error(`${this.props.name}加载失败`, error, info.componentStack);
    }

    render() {
        if (!this.state.failed) return this.props.children;
        return <section className="page-load-error" role="alert" aria-label={`${this.props.name}加载失败`}>
            <h2><WarningCircle size={20} aria-hidden="true" />{this.props.name}加载失败</h2>
            <p>请刷新页面重试。</p>
            <Button highContrast variant="soft" onClick={() => window.location.reload()}><ArrowsClockwise />刷新页面</Button>
        </section>;
    }
}
