// 正在运行的服务仍返回这些提示原文。在展示层统一处理，无需中断任务或重启服务。
const NOTICE_TEXT: Record<string, string> = {
    "跨版本执行：历史与当前规则不完全一致，不作同版本标准比较": "",
    "当前回放暂不可读或格式不符，保留上次成功读取的曲线": "曲线更新失败，暂时显示上次的数据",
    "最近存档写入失败，曲线可能落后于运行状态": "进度保存失败，曲线可能还没更新",
    "尚未生成回放存档，等待首个可读取的结算记录": "",
    "该 run 未生成回放存档，无法提取曲线": "",
    "整局归档轨迹尚不可用，目前只覆盖当前运行阶段，不能据此断言此前未购买或制作": "暂时只能查看本次启动后的操作",
    "最近检查点保存失败，已记录操作可能未进入恢复点，续跑时以恢复位置为准": "进度保存失败，续跑时可能丢失最近的操作",
};

export function runNotice(message: string | null | undefined): string | null {
    if (!message) return null;
    return message.split("。").map(part => NOTICE_TEXT[part] ?? part).filter(Boolean).join("。") || null;
}
