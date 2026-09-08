"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { api, ApiError, type CourseDetail } from "@/lib/api";
import { formatDueDate, outlineBadge, urgencyClasses } from "@/lib/format";

export default function CourseDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const courseId = Number(params.id);

  const [course, setCourse] = useState<CourseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showOutlineText, setShowOutlineText] = useState(false);
  const [savingDifficulty, setSavingDifficulty] = useState(false);
  const [outlineUrlInput, setOutlineUrlInput] = useState("");
  const [submittingOutline, setSubmittingOutline] = useState(false);
  const [outlineSubmitError, setOutlineSubmitError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const data = await api.course(courseId);
        setCourse(data);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          router.push("/login");
          return;
        }
        setError(err instanceof ApiError ? err.message : "Failed to load course");
      } finally {
        setLoading(false);
      }
    })();
  }, [courseId, router]);

  async function handleDifficultyChange(value: number) {
    if (!course) return;
    setSavingDifficulty(true);
    try {
      const updated = await api.updateCourseDifficulty(course.id, value);
      setCourse({ ...course, difficulty: updated.difficulty });
    } finally {
      setSavingDifficulty(false);
    }
  }

  async function handleSubmitOutline(e: FormEvent) {
    e.preventDefault();
    if (!course || !outlineUrlInput.trim()) return;
    setSubmittingOutline(true);
    setOutlineSubmitError(null);
    try {
      const outline = await api.submitCourseOutline(course.id, outlineUrlInput.trim());
      setCourse({ ...course, outline });
      setOutlineUrlInput("");
      setShowOutlineText(false);
    } catch (err) {
      setOutlineSubmitError(err instanceof ApiError ? err.message : "Could not fetch that outline");
    } finally {
      setSubmittingOutline(false);
    }
  }

  if (loading) return <p className="text-sm text-slate-500">Loading...</p>;
  if (error) return <p className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>;
  if (!course) return null;

  const badge = outlineBadge(course.outline?.confidence ?? 0);

  return (
    <div className="space-y-8">
      <div>
        <Link href="/" className="text-sm text-blue-600 hover:underline">
          &larr; Back to dashboard
        </Link>
        <div className="mt-2 flex items-start justify-between">
          <div>
            <h1 className="text-xl font-semibold text-slate-800">{course.name}</h1>
            <p className="text-sm text-slate-500">
              {course.course_code} {course.term ? `· ${course.term}` : ""}
            </p>
          </div>
          {course.html_url && (
            <a
              href={course.html_url}
              target="_blank"
              rel="noreferrer"
              className="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
            >
              Open in Canvas
            </a>
          )}
        </div>
      </div>

      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex items-center justify-between">
          <h2 className="font-medium text-slate-800">Subjective difficulty</h2>
          <span className="text-xs text-slate-400">used later by the prioritization agent</span>
        </div>
        <div className="mt-2 flex gap-1">
          {[1, 2, 3, 4, 5].map((v) => (
            <button
              key={v}
              onClick={() => handleDifficultyChange(v)}
              disabled={savingDifficulty}
              className={`h-8 w-8 rounded text-sm font-medium ${
                course.difficulty === v
                  ? "bg-indigo-600 text-white"
                  : "bg-slate-100 text-slate-600 hover:bg-slate-200"
              }`}
            >
              {v}
            </button>
          ))}
        </div>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex items-center justify-between">
          <h2 className="font-medium text-slate-800">Course outline</h2>
          <span className={`rounded px-2 py-0.5 text-xs ${badge.classes}`}>{badge.label}</span>
        </div>

        <form onSubmit={handleSubmitOutline} className="mt-3 flex gap-2">
          <input
            type="url"
            value={outlineUrlInput}
            onChange={(e) => setOutlineUrlInput(e.target.value)}
            placeholder="Paste the link to your course outline/syllabus"
            className="flex-1 rounded border border-slate-300 px-3 py-1.5 text-sm"
            required
          />
          <button
            type="submit"
            disabled={submittingOutline}
            className="shrink-0 rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {submittingOutline ? "Fetching..." : course.outline && course.outline.source !== "none" ? "Replace" : "Submit"}
          </button>
        </form>
        {outlineSubmitError && (
          <p className="mt-2 rounded bg-red-50 px-3 py-2 text-sm text-red-700">{outlineSubmitError}</p>
        )}

        {course.outline && course.outline.source !== "none" ? (
          <div className="mt-3 space-y-2 text-sm">
            <p className="text-slate-600">
              {course.outline.source === "user_submitted" ? "Submitted by you" : "Auto-detected"}
              {course.outline.title ? <> — &ldquo;{course.outline.title}&rdquo;</> : null}
            </p>
            <div className="flex flex-wrap gap-2">
              {course.outline.canvas_url && (
                <a
                  href={course.outline.canvas_url}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-100"
                >
                  View in Canvas
                </a>
              )}
              {course.outline.external_url && (
                <a
                  href={course.outline.external_url}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded border border-blue-300 px-2 py-1 text-xs text-blue-700 hover:bg-blue-50"
                >
                  Open external outline ↗ ({new URL(course.outline.external_url).hostname})
                </a>
              )}
            </div>
            {course.outline.fetched_content && (
              <div>
                <button
                  onClick={() => setShowOutlineText((s) => !s)}
                  className="text-xs text-blue-600 hover:underline"
                >
                  {showOutlineText ? "Hide extracted text" : "Show extracted text"}
                </button>
                {showOutlineText && (
                  <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-3 text-xs text-slate-600">
                    {course.outline.fetched_content}
                  </pre>
                )}
              </div>
            )}
          </div>
        ) : (
          <p className="mt-3 text-sm text-slate-500">
            No outline yet — paste the link above (the Canvas page, a Canvas file, or an external site
            like GitHub Pages all work). It's fetched once and stored; paste again any time to replace it.
          </p>
        )}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="font-medium text-slate-800">Course description</h2>
        {course.description_text ? (
          <div className="mt-2 space-y-2 text-sm">
            <p className="text-slate-600">{course.description_text}</p>
            {course.description_url && (
              <a
                href={course.description_url}
                target="_blank"
                rel="noreferrer"
                className="inline-block rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-100"
              >
                View on UAlberta Catalogue ↗
              </a>
            )}
          </div>
        ) : (
          <p className="mt-2 text-sm text-slate-500">
            No official course description found yet — this is pulled automatically from the UAlberta
            course catalogue during sync.
          </p>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-lg font-medium text-slate-800">Assignments</h2>
        {course.assignments.length === 0 ? (
          <p className="text-sm text-slate-500">No assignments found for this course.</p>
        ) : (
          <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-4 py-2">Assignment</th>
                  <th className="px-4 py-2">Due</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Points</th>
                </tr>
              </thead>
              <tbody>
                {course.assignments.map((a) => (
                  <tr key={a.id} className="border-t border-slate-100">
                    <td className="px-4 py-2">
                      {a.html_url ? (
                        <a href={a.html_url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
                          {a.name}
                        </a>
                      ) : (
                        a.name
                      )}
                    </td>
                    <td className="px-4 py-2">
                      <span className={`rounded px-2 py-0.5 text-xs ${urgencyClasses(a.due_at)}`}>
                        {formatDueDate(a.due_at)}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-slate-600">{a.submission_status ?? "—"}</td>
                    <td className="px-4 py-2 text-slate-600">
                      {a.score != null ? `${a.score}/${a.points_possible ?? "?"}` : a.points_possible ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
