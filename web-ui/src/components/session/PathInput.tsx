"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { completePath, PathCompletion } from "@/lib/api/filesystem";

interface PathInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}

export function PathInput({ value, onChange, placeholder, className }: PathInputProps) {
  const [completions, setCompletions] = useState<PathCompletion[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [cwd, setCwd] = useState<string>("");
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Fetch completions when value changes
  const fetchCompletions = useCallback(async (path: string) => {
    setIsLoading(true);
    try {
      const result = await completePath(path);
      setCompletions(result.completions || []);
      if (result.cwd) setCwd(result.cwd);
      setSelectedIndex(0);
    } catch (err) {
      console.error("Path completion error:", err);
      setCompletions([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Debounced fetch
  useEffect(() => {
    const timer = setTimeout(() => {
      if (showDropdown || value) {
        fetchCompletions(value);
      }
    }, 150);
    return () => clearTimeout(timer);
  }, [value, showDropdown, fetchCompletions]);

  // Handle keyboard navigation
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!showDropdown || completions.length === 0) {
      if (e.key === "ArrowDown" || e.key === "Tab") {
        e.preventDefault();
        setShowDropdown(true);
        fetchCompletions(value);
      }
      return;
    }

    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setSelectedIndex(i => Math.min(i + 1, completions.length - 1));
        break;
      case "ArrowUp":
        e.preventDefault();
        setSelectedIndex(i => Math.max(i - 1, 0));
        break;
      case "Tab":
      case "Enter":
        e.preventDefault();
        if (completions[selectedIndex]) {
          const selected = completions[selectedIndex];
          onChange(selected.path + "/");
          setShowDropdown(false);
        }
        break;
      case "Escape":
        setShowDropdown(false);
        break;
    }
  };

  // Handle click outside to close dropdown
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        dropdownRef.current && 
        !dropdownRef.current.contains(e.target as Node) &&
        inputRef.current &&
        !inputRef.current.contains(e.target as Node)
      ) {
        setShowDropdown(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Scroll selected item into view
  useEffect(() => {
    if (dropdownRef.current && showDropdown) {
      const selected = dropdownRef.current.querySelector(`[data-index="${selectedIndex}"]`);
      selected?.scrollIntoView({ block: "nearest" });
    }
  }, [selectedIndex, showDropdown]);

  return (
    <div className="relative">
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={e => {
          onChange(e.target.value);
          setShowDropdown(true);
        }}
        onFocus={() => setShowDropdown(true)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        className={className}
        autoComplete="off"
        spellCheck={false}
      />
      
      {/* Loading indicator */}
      {isLoading && (
        <div className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 text-xs">
          ...
        </div>
      )}

      {/* Dropdown */}
      {showDropdown && completions.length > 0 && (
        <div
          ref={dropdownRef}
          className="absolute z-50 w-full mt-1 bg-gray-800 border border-gray-600 rounded shadow-lg max-h-48 overflow-y-auto"
        >
          {/* Current directory hint */}
          {cwd && !value && (
            <div className="px-3 py-1.5 text-xs text-gray-500 border-b border-gray-700">
              当前目录: {cwd}
            </div>
          )}
          
          {completions.map((item, index) => (
            <div
              key={item.path}
              data-index={index}
              className={`px-3 py-2 cursor-pointer flex items-center gap-2 ${
                index === selectedIndex 
                  ? "bg-blue-600 text-white" 
                  : "text-gray-300 hover:bg-gray-700"
              }`}
              onClick={() => {
                onChange(item.path + "/");
                setShowDropdown(false);
                inputRef.current?.focus();
              }}
            >
              <span className="text-sm">📁</span>
              <span className="text-sm truncate">{item.name}</span>
              <span className="text-xs text-gray-500 ml-auto truncate max-w-[200px]">
                {item.path}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* No results hint */}
      {showDropdown && !isLoading && completions.length === 0 && value && (
        <div className="absolute z-50 w-full mt-1 bg-gray-800 border border-gray-600 rounded shadow-lg">
          <div className="px-3 py-2 text-sm text-gray-500">
            没有匹配的目录
          </div>
        </div>
      )}
    </div>
  );
}
