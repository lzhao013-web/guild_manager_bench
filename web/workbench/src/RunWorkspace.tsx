import { useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ArrowLeft, SquaresFour } from "@phosphor-icons/react";
import { GameScene } from "./GameScene";
import { RunOverview } from "./RunOverview";
import { MODULE_NAMES, emptySceneView, openSceneView } from "./sceneNavigation";
import type { RunModule, SceneModule, SceneViews } from "./sceneNavigation";
import type { Selection } from "./sceneModel";
import type { Observation } from "./types";
import type { WatchFrame, WatchRecord } from "./watchModel";
import "./workspace.css";

export function RunWorkspace({
    observation,
    before,
    frames,
    records,
    activeModule,
    onModule,
    onFrame,
    actions,
    models,
    initialSelection,
    live = false,
}: {
    observation: Observation | null;
    before: Observation | null;
    frames: WatchFrame[];
    records: WatchRecord[];
    activeModule: RunModule | null;
    onModule: (module: RunModule | null) => void;
    onFrame: (frame: WatchFrame) => void;
    actions: ReactNode;
    models: ReactNode;
    initialSelection?: Selection;
    live?: boolean;
}) {
    const [visited, setVisited] = useState<RunModule[]>(() => activeModule ? [activeModule] : []);
    const [views, setViews] = useState<SceneViews>(() => activeModule && activeModule !== "actions" && activeModule !== "models"
        ? openSceneView({}, activeModule, observation, initialSelection) : {});
    const overview = useRef<HTMLDivElement>(null);
    const overviewScroll = useRef(0);
    const heading = useRef<HTMLHeadingElement>(null);
    const returnFocus = useRef<HTMLElement | null>(null);
    const previousModule = useRef<RunModule | null>(null);
    useLayoutEffect(() => {
        if (activeModule === null) {
            const panel = overview.current!;
            panel.scrollTop = overviewScroll.current;
            if (previousModule.current) {
                // 历史位置变化可能移除了原先点选的对象，此时返回对应模块入口。
                const target = returnFocus.current?.isConnected
                    ? returnFocus.current
                    : panel.querySelector<HTMLElement>(
                          `[data-module-target="${previousModule.current}"]`,
                      )!;
                target.focus({ preventScroll: true });
            }
        } else {
            // 工具推理和对象轨迹也会从外部切换模块，统一在这里保留已访问内容。
            setVisited((previous) => previous.includes(activeModule) ? previous : [...previous, activeModule]);
            if (!document.activeElement?.closest(".workspace-module-toolbar"))
                heading.current!.focus({ preventScroll: true });
        }
        previousModule.current = activeModule;
    }, [activeModule]);
    function open(module: RunModule, selection?: Selection, slot?: string) {
        if (activeModule === null && overview.current!.contains(document.activeElement))
            returnFocus.current = document.activeElement as HTMLElement;
        if (module !== "actions" && module !== "models")
            setViews((previous) => openSceneView(previous, module, observation, selection, slot));
        onModule(module);
    }
    const mounted =
        activeModule && !visited.includes(activeModule) ? [...visited, activeModule] : visited;
    return (
        <div className={`run-workspace ${activeModule ? "module-expanded" : "at-overview"}`}>
            {activeModule && (
                <header className="workspace-module-toolbar">
                    <button className="workspace-return" onClick={() => onModule(null)}>
                        <ArrowLeft size={16} />
                        返回总览
                    </button>
                    <span className="workspace-module-divider" />
                    <SquaresFour size={15} />
                    <h3
                        ref={heading}
                        tabIndex={-1}
                        aria-label={`${MODULE_NAMES[activeModule]}模块标题`}
                    >
                        {MODULE_NAMES[activeModule]}
                    </h3>
                    <label>
                        <span>切换模块</span>
                        <select
                            aria-label="切换展开模块"
                            value={activeModule}
                            onChange={(event) => open(event.target.value as RunModule)}
                        >
                            {Object.entries(MODULE_NAMES).map(([key, name]) => (
                                <option key={key} value={key}>
                                    {name}
                                </option>
                            ))}
                        </select>
                    </label>
                </header>
            )}
            <div
                className="workspace-overview-panel"
                hidden={activeModule !== null}
                ref={overview}
                onScroll={(event) => {
                    if (activeModule === null)
                        overviewScroll.current = event.currentTarget.scrollTop;
                }}
            >
                <RunOverview
                    observation={observation}
                    frames={frames}
                    records={records} live={live}
                    onOpen={open}
                    onFrame={onFrame}
                />
            </div>
            {mounted.map((module) => (
                <section
                    key={module}
                    className={`workspace-module-panel module-${module}`}
                    aria-label={`${MODULE_NAMES[module]}模块`}
                    hidden={activeModule !== module}
                >
                    {module === "actions" ? (
                        actions
                    ) : module === "models" ? (
                        models
                    ) : (
                        <GameScene
                            observation={observation}
                            before={before}
                            module={module as SceneModule}
                            view={views[module] || emptySceneView}
                            onView={(view) =>
                                setViews((previous) => ({ ...previous, [module]: view }))
                            }
                            onNavigate={open}
                            active={activeModule === module}
                        />
                    )}
                </section>
            ))}
        </div>
    );
}
