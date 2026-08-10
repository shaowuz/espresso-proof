import { useState } from "react";
import { MessageSquare, Pin, Sparkles } from "lucide-react";
import { postResearchNote } from "../lib/api";
import { MathText } from "./MathText";
import { FadeIn } from "./motion";
import type { ActiveObject } from "../lib/types";

export type ChatMessage = {
  id: number;
  role: "you" | "espresso";
  text: string;
  activeObject: ActiveObject;
};

/**
 * A conversational PREVIEW panel. The streamed text is advisory — it
 * is not evidence, not proof, not source support, and it never updates the
 * evidence panels (those come only from validated artifacts via TanStack
 * Query). The only command entry point is the bottom composer; this panel is
 * display-only history/preview so the cockpit has one clear input layer.
 */
export function ChatPanel({
  runId,
  activeObject,
  messages,
  lastNote,
}: {
  runId: string;
  activeObject: ActiveObject;
  messages: ChatMessage[];
  lastNote: string;
}) {
  const [pinned, setPinned] = useState<string | null>(null);

  async function pinToRoute() {
    if (!activeObject || lastNote.trim() === "") return;
    try {
      await postResearchNote(runId, {
        target_kind: activeObject.kind,
        target_id: activeObject.id,
        text: lastNote.trim(),
      });
      setPinned(`Noted on "${activeObject.title}" — advisory, changes no evidence.`);
      setTimeout(() => setPinned(null), 3000);
    } catch {
      setPinned("Could not save the note.");
      setTimeout(() => setPinned(null), 3000);
    }
  }

  return (
    <section className="mt-4" aria-label="Conversation preview">
      <h2 className="mb-1.5 flex items-center gap-1.5 text-[14px] font-semibold uppercase tracking-wide text-stone-500">
        <MessageSquare className="h-3.5 w-3.5" aria-hidden /> Conversation
        <span className="ml-1 inline-flex items-center gap-1 rounded-full border border-stone-200 bg-stone-50 px-1.5 py-0.5 text-[12px] font-normal normal-case tracking-normal text-stone-500">
          <Sparkles className="h-2.5 w-2.5" aria-hidden /> preview — not evidence
        </span>
      </h2>

      <div className="rounded-lg border border-stone-200 bg-white">
        <div className="max-h-64 space-y-2 overflow-y-auto p-3">
          {messages.length === 0 ? (
            <p className="text-[14px] text-stone-400">
              {activeObject
                ? `Use the bottom composer to ask about active ${activeObject.kind}: ${activeObject.title}.`
                : "Use the bottom composer to ask about this run."}{" "}
              Replies appear here as a conversational preview of the current artifacts and
              never change evidence.
            </p>
          ) : (
            messages.map((m) =>
              m.role === "you" ? (
                // The user's message is a compact right-aligned
                // bubble; context is a small muted chip inside it, not a
                // shouting uppercase banner above it.
                <FadeIn key={m.id} motionKey={`${m.id}-${m.text.length}`}>
                  <div className="flex justify-end">
                    <div className="max-w-[85%] rounded-2xl rounded-br-md bg-stone-100 px-3 py-2 text-[14px] leading-relaxed text-stone-800">
                      {m.activeObject && (
                        <p className="mb-1 truncate text-[12px] text-stone-400">
                          re: {m.activeObject.kind} · {m.activeObject.title}
                        </p>
                      )}
                      <MathText>{m.text}</MathText>
                    </div>
                  </div>
                </FadeIn>
              ) : (
                // The assistant reply is a plain readable block — no name
                // prefix, no bubble; the last line (the guidance) stands out.
                <FadeIn key={m.id} motionKey={`${m.id}-${m.text.length}`}>
                  <div className="max-w-[95%] text-[14px] leading-relaxed text-stone-700">
                    {m.text === "" ? (
                      <span className="text-stone-400">…</span>
                    ) : (
                      m.text.split("\n").map((line, i) => (
                        <p
                          key={i}
                          className={
                            line.startsWith("Next step:") ||
                            line.startsWith("This page is already working")
                              ? "mt-1.5 rounded-md border border-sage-200 bg-sage-50/60 px-2 py-1 font-medium text-sage-900"
                              : line.startsWith("This is a conversational preview")
                                ? "mt-1 text-[12px] text-stone-400"
                                : "mb-0.5"
                          }
                        >
                          <MathText>{line}</MathText>
                        </p>
                      ))
                    )}
                  </div>
                </FadeIn>
              ),
            )
          )}
        </div>

        {/* Advisory note provenance: pin the last question to the active object.
            The note never changes evidence; any future influence is advisory. */}
        {activeObject && lastNote.trim() !== "" && (
          <div className="flex flex-wrap items-center gap-2 border-t border-stone-100 px-2 py-1.5">
            <button
              type="button"
              onClick={() => void pinToRoute()}
              className="inline-flex items-center gap-1 rounded-md border border-stone-200 bg-white px-1.5 py-0.5 text-[13px] font-medium text-stone-600 hover:bg-stone-50"
            >
              <Pin className="h-3 w-3" aria-hidden /> Note on active object
            </button>
            {pinned && <span className="text-[13px] text-stone-400">{pinned}</span>}
          </div>
        )}
      </div>
    </section>
  );
}
