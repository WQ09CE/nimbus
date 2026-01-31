"use client";

import { useEffect, useRef, useCallback } from "react";

interface UseAutoScrollOptions {
  enabled?: boolean;
  threshold?: number;
  smooth?: boolean;
  throttleMs?: number;
}

export function useAutoScroll({
  enabled = true,
  threshold = 50,
  smooth = true,
  throttleMs = 100,
}: UseAutoScrollOptions = {}) {
  const elementRef = useRef<HTMLDivElement>(null);
  const lastScrollTime = useRef(0);
  const scrollTimeoutRef = useRef<NodeJS.Timeout>();

  const scrollToBottom = useCallback((force = false) => {
    if (!elementRef.current || (!enabled && !force)) return;

    const now = Date.now();
    
    // Throttle scroll calls
    if (!force && now - lastScrollTime.current < throttleMs) {
      // Clear existing timeout and set a new one
      if (scrollTimeoutRef.current) {
        clearTimeout(scrollTimeoutRef.current);
      }
      
      scrollTimeoutRef.current = setTimeout(() => {
        scrollToBottom(true);
      }, throttleMs);
      return;
    }

    lastScrollTime.current = now;

    try {
      elementRef.current.scrollIntoView({
        behavior: smooth ? "smooth" : "auto",
        block: "end",
        inline: "nearest",
      });
    } catch (error) {
      // Fallback for older browsers
      elementRef.current.scrollTop = elementRef.current.scrollHeight;
    }
  }, [enabled, smooth, throttleMs]);

  const isAtBottom = useCallback(() => {
    if (!elementRef.current) return true;
    
    const container = elementRef.current.parentElement;
    if (!container) return true;

    const { scrollTop, scrollHeight, clientHeight } = container;
    return scrollHeight - scrollTop - clientHeight <= threshold;
  }, [threshold]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (scrollTimeoutRef.current) {
        clearTimeout(scrollTimeoutRef.current);
      }
    };
  }, []);

  return {
    elementRef,
    scrollToBottom,
    isAtBottom,
  };
}

interface UseScrollDetectionOptions {
  threshold?: number;
  onScrollUp?: () => void;
  onScrollDown?: () => void;
  onReachTop?: () => void;
  onReachBottom?: () => void;
}

export function useScrollDetection({
  threshold = 50,
  onScrollUp,
  onScrollDown,
  onReachTop,
  onReachBottom,
}: UseScrollDetectionOptions = {}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const lastScrollTop = useRef(0);
  const lastScrollDirection = useRef<'up' | 'down' | null>(null);

  const handleScroll = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;

    const { scrollTop, scrollHeight, clientHeight } = container;
    const isAtTop = scrollTop <= threshold;
    const isAtBottom = scrollHeight - scrollTop - clientHeight <= threshold;

    // Determine scroll direction
    const scrollDirection = scrollTop > lastScrollTop.current ? 'down' : 'up';
    
    // Only call callbacks if direction changed to avoid spam
    if (scrollDirection !== lastScrollDirection.current) {
      if (scrollDirection === 'up' && onScrollUp) {
        onScrollUp();
      } else if (scrollDirection === 'down' && onScrollDown) {
        onScrollDown();
      }
      lastScrollDirection.current = scrollDirection;
    }

    // Check if reached top/bottom
    if (isAtTop && onReachTop) {
      onReachTop();
    }
    if (isAtBottom && onReachBottom) {
      onReachBottom();
    }

    lastScrollTop.current = scrollTop;
  }, [threshold, onScrollUp, onScrollDown, onReachTop, onReachBottom]);

  const isAtBottom = useCallback(() => {
    if (!containerRef.current) return true;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    return scrollHeight - scrollTop - clientHeight <= threshold;
  }, [threshold]);

  const scrollToTop = useCallback(() => {
    if (containerRef.current) {
      containerRef.current.scrollTo({ top: 0, behavior: 'smooth' });
    }
  }, []);

  const scrollToBottom = useCallback(() => {
    if (containerRef.current) {
      containerRef.current.scrollTo({ 
        top: containerRef.current.scrollHeight, 
        behavior: 'smooth' 
      });
    }
  }, []);

  return {
    containerRef,
    handleScroll,
    isAtBottom,
    scrollToTop,
    scrollToBottom,
  };
}