"use client";
import { useEffect, useRef } from "react";

export function useKeyboardShortcut(
  key: string,
  callback: () => void,
  modifier = false,
) {
  const action = useRef(callback);
  useEffect(() => {
    action.current = callback;
  }, [callback]);
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (event.key !== key || (modifier && !event.metaKey && !event.ctrlKey))
        return;
      event.preventDefault();
      action.current();
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [key, modifier]);
}
