import { NextResponse } from "next/server";
import { AuthenticationError } from "./auth";
import { ControlPlaneConflictError } from "./store";

export function apiError(error: unknown): NextResponse {
  if (error instanceof AuthenticationError) {
    return NextResponse.json({ error: error.message }, { status: 401 });
  }
  if (error instanceof ControlPlaneConflictError) {
    return NextResponse.json({ error: error.message }, { status: 409 });
  }
  console.error(error);
  return NextResponse.json(
    {
      error:
        error instanceof Error ? error.message : "Unexpected server error.",
    },
    { status: 500 },
  );
}

export async function jsonBody(
  request: Request,
): Promise<Record<string, unknown>> {
  try {
    const value = await request.json();
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("Request body must be a JSON object.");
    }
    return value as Record<string, unknown>;
  } catch (error) {
    if (error instanceof Error && error.message.includes("JSON object")) {
      throw error;
    }
    throw new Error("Request body must contain valid JSON.");
  }
}

export function textField(
  body: Record<string, unknown>,
  name: string,
  options: { min?: number; max?: number } = {},
): string {
  const value = typeof body[name] === "string" ? body[name].trim() : "";
  const minimum = options.min ?? 1;
  const maximum = options.max ?? 10_000;
  if (value.length < minimum || value.length > maximum) {
    throw new Error(
      `${name} must be between ${minimum} and ${maximum} characters.`,
    );
  }
  return value;
}

export function boundedInteger(
  body: Record<string, unknown>,
  name: string,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  const raw = body[name];
  if (raw === undefined || raw === null || raw === "") return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${name} must be an integer from ${minimum} to ${maximum}.`);
  }
  return value;
}
