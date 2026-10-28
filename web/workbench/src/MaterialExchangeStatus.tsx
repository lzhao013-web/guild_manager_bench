import type { Observation } from "./types";

export function MaterialExchangeStatus({ turn, exchange }: {
    turn: number;
    exchange: NonNullable<Observation["material_exchange"]>;
}) {
    const text = turn < exchange.from_turn ? `未开放（T${exchange.from_turn}起）`
        : exchange.remaining_points === 0 ? `额度已用尽（0/${exchange.point_limit}点）`
        : `剩余额度${exchange.remaining_points}/${exchange.point_limit}点`;
    return <span aria-label="材料兑换状态">材料兑换：{text}</span>;
}
