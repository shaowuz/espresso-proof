import { useRef, useState } from "react";
import { MessageSquare, Pin, Send, Sparkles } from "lucide-react";
import { streamChat } from "../lib/chat";
import { postResearchNote } from "../lib/api";
import { MathText } from "./MathText";
import { FadeIn } from "./motion";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";

type Msg = { id: number; role: "you" | "espresso"; text: string };

/**
 * Goal 43F: a conversational PREVIEW panel. The streamed text is advisory — it
 * is not evidence, not proof, not source support, and it never updates the
 * evidence panels (those come only from validated artifacts via TanStack
 * Query). If streaming fails, a single fallback line is shown and the rest of
 * the cockpit keeps working from polling.
 */
export function ChatPanel({
  runId,
  selectedRoute,
}: {
  runId: string;
  selectedRoute: { id: string; title: string } | null;
}) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [lastNote, setLastNote] = useState("");
  const [pinned, setPinned] = useState<string | null>(null);
  const nextId = useRef(0);

  async function pinToRoute() {
    if (!selectedRoute || lastNote.trim() === "") return;
    try {
      await postResearchNote(runId, {
        target_kind: "strategy",
        target_id: selectedRoute.id,
        text: lastNote.trim(),
      });
      setPinned(`Noted on "${selectedRoute.title}" — advisory, changes no evidence.`);
      setTimeout(() => setPinned(null), 3000);
    } catch {
      setPinned("Could not save the note.");
      setTimeout(() => setPinned(null), 3000);
    }
  }

  function append(role: Msg["role"], text: string): number {
    const id = nextId.current++;
    setMessages((m) => [...m, { id, role, text }]);
    return id;
  }

  async function send() {
    const msg = input.trim();
    if (msg === "" || streaming) return;
    setInput("");
    setLastNote(msg); // the most recent question, available to pin as a note
    append("you", msg);
    setStreaming(true);
    const replyId = append("espresso", "");
    let any = false;
    await streamChat(
      runId,
      msg,
      (line) => {
        any = true;
        setMessages((m) =>
          m.map((x) =>
            x.id === replyId ? { ...x, text: x.text === "" ? line : `${x.text}\n${line}` } : x,
          ),
        );
      },
      (ok) => {
        if (!ok && !any) {
          // fallback: streaming unavailable — the panels remain the truth.
          setMessages((m) =>
            m.map((x) =>
              x.id === replyId
                ? {
                    ...x,
                    text:
                      "Streaming is unavailable right now — the panels above remain the source of truth. Try the agent buttons to update evidence.",
                  }
                : x,
            ),
          );
        }
        setStreaming(false);
      },
    );
  }

  return (
    <section className="mt-4">
      <h2 className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-stone-500">
        <MessageSquare className="h-3.5 w-3.5" aria-hidden /> Conversation
        <span className="ml-1 inline-flex items-center gap-1 rounded-full border border-stone-200 bg-stone-50 px-1.5 py-0.5 text-[10px] font-normal normal-case tracking-normal text-stone-500">
          <Sparkles className="h-2.5 w-2.5" aria-hidden /> preview — not evidence
        </span>
      </h2>

      <div className="rounded-lg border border-stone-200 bg-white">
        <div className="max-h-64 space-y-2 overflow-y-auto p-3">
          {messages.length === 0 ? (
            <p className="text-xs text-stone-400">
              Ask about this run — strategies, examples, what is missing. Replies are a
              conversational preview of the current artifacts and never change evidence.
            </p>
          ) : (
            messages.map((m) => (
              <FadeIn key={m.id} motionKey={`${m.id}-${m.text.length}`}>
                <div className="text-xs leading-relaxed">
                  <span
                    className={
                      m.role === "you"
                        ? "font-semibold text-stone-700"
                        : "font-semibold text-sage-700"
                    }
                  >
                    {m.role === "you" ? "You" : "Espresso"}:{" "}
                  </span>
                  {m.text === "" ? (
                    <span className="text-stone-400">…</span>
                  ) : (
                    m.text.split("\n").map((line, i) => (
                      <span key={i} className="block text-stone-700">
                        <MathText>{line}</MathText>
                      </span>
                    ))
                  )}
                </div>
              </FadeIn>
            ))
          )}
        </div>

        <div className="flex items-end gap-2 border-t border-stone-100 p-2">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                void send();
              }
            }}
            rows={1}
            placeholder="Ask about this run (⌘↵)…"
            disabled={streaming}
            className="min-h-[34px] flex-1 resize-none text-xs"
            aria-label="Ask Espresso about this run"
          />
          <Button
            size="sm"
            onClick={() => void send()}
            disabled={streaming || input.trim() === ""}
            aria-label="Send chat message"
          >
            <Send className="h-3.5 w-3.5" aria-hidden />
          </Button>
        </div>

        {/* advisory note provenance: pin the last question to the selected
            route. The note never changes evidence; its influence (if any) is
            advisory, surfaced in the route's time-allocation ledger. */}
        {selectedRoute && lastNote.trim() !== "" && (
          <div className="flex flex-wrap items-center gap-2 border-t border-stone-100 px-2 py-1.5">
            <button
              type="button"
              onClick={() => void pinToRoute()}
              className="inline-flex items-center gap-1 rounded-md border border-stone-200 bg-white px-1.5 py-0.5 text-[11px] font-medium text-stone-600 hover:bg-stone-50"
            >
              <Pin className="h-3 w-3" aria-hidden /> Note on selected route
            </button>
            {pinned && <span className="text-[11px] text-stone-400">{pinned}</span>}
          </div>
        )}
      </div>
    </section>
  );
}
