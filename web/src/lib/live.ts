"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Alert } from "./api";

export type LiveState = "connecting" | "live" | "offline";
export type LiveEvent =
  | { type: "alert"; alert: Alert }
  | { type: "window"; sensor_id: string; window_start: number | null };

const WS_URL = process.env.NEXT_PUBLIC_BOTGRAPH_WS_URL ?? "ws://127.0.0.1:8000/api/ws";

/**
 * Keeps one WebSocket to the API open while signed in. Each push invalidates the queries it
 * affects, so every page refreshes itself; reconnects with backoff (max 30 s).
 */
export function useLive(token: string | null, onAlert?: (a: Alert) => void): LiveState {
  const queryClient = useQueryClient();
  const [state, setState] = useState<LiveState>("connecting");

  useEffect(() => {
    if (!token) return;
    let socket: WebSocket | null = null;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let closed = false;

    const connect = () => {
      setState("connecting");
      // The token goes in the first message, never the URL (URLs end up in logs).
      socket = new WebSocket(WS_URL);
      socket.onopen = () => socket?.send(JSON.stringify({ type: "auth", token }));
      socket.onmessage = (msg) => {
        const event = JSON.parse(msg.data) as LiveEvent | { type: "ready" };
        if (event.type === "ready") {
          retry = 0;
          setState("live");
          return;
        }
        if (event.type === "alert") {
          const parsed = Alert.safeParse(event.alert);
          if (parsed.success) onAlert?.(parsed.data);
          queryClient.invalidateQueries({ queryKey: ["alerts"] });
          queryClient.invalidateQueries({ queryKey: ["host"] });
        }
        queryClient.invalidateQueries({ queryKey: ["overview"] });
        queryClient.invalidateQueries({ queryKey: ["sensors"] });
        if (event.type === "window") queryClient.invalidateQueries({ queryKey: ["graph"] });
      };
      socket.onclose = (e) => {
        setState("offline");
        if (closed || e.code === 4401) return; // unmounted, or the token was rejected
        timer = setTimeout(connect, Math.min(30_000, 1000 * 2 ** retry++));
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      socket?.close();
    };
  }, [token, queryClient, onAlert]);

  return token ? state : "offline";
}
