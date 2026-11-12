"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";

import { StackLogo, StatusWord } from "@/components/shell";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import type { ExportState } from "./types";

function CodeBlock({ text, name }: { text: string; name: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex items-center justify-between gap-3">
        <span className="font-mono text-[0.8125rem] text-muted-foreground">{name}</span>
        <Button variant="secondary" size="sm" onClick={copy} className="min-w-24">
          {copied ? <Check aria-hidden className="text-ok" /> : <Copy aria-hidden />}
          <StatusWord>{copied ? "Copied" : "Copy"}</StatusWord>
        </Button>
      </div>
      <pre
        tabIndex={0}
        className="no-scrollbar min-h-0 flex-1 overflow-auto rounded-lg bg-bg px-3.5 py-3 font-mono text-[0.8125rem] leading-relaxed"
      >
        {text}
      </pre>
    </div>
  );
}

/** "Export as code": the same pipeline as a Python script and as blueprint JSON. */
export function ExportSheet({
  open,
  onOpenChange,
  state,
  onRetry,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  state: ExportState;
  onRetry: () => void;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-2xl"
      >
        <SheetHeader className="pr-14">
          <SheetTitle>Export as code</SheetTitle>
          <SheetDescription>
            The same pipeline as a script that calls the gaik components, and as a blueprint.
          </SheetDescription>
        </SheetHeader>
        <div className="flex min-h-0 flex-1 flex-col px-4 pb-4">
          {state.loading && (
            <div className="flex flex-col gap-2.5 pt-2" aria-label="Loading">
              <Skeleton className="h-9 w-44" />
              <Skeleton className="h-64 w-full" />
            </div>
          )}
          {state.error && (
            <div className="flex flex-col items-start gap-3 pt-2">
              <p role="alert" className="text-danger">
                {state.error}
              </p>
              <Button variant="secondary" size="sm" onClick={onRetry}>
                Try again
              </Button>
            </div>
          )}
          {state.python !== null && state.blueprint !== null && (
            <Tabs defaultValue="python" className="flex min-h-0 flex-1 flex-col gap-3">
              <TabsList>
                <TabsTrigger value="python" className="gap-2 px-3">
                  <StackLogo id="python" size={16} />
                  Python
                </TabsTrigger>
                <TabsTrigger value="blueprint" className="px-3">
                  Blueprint
                </TabsTrigger>
              </TabsList>
              <TabsContent value="python" className="flex min-h-0 flex-1 flex-col">
                <CodeBlock text={state.python} name="pipeline.py" />
              </TabsContent>
              <TabsContent value="blueprint" className="flex min-h-0 flex-1 flex-col">
                <CodeBlock text={state.blueprint} name="blueprint.json" />
              </TabsContent>
            </Tabs>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
