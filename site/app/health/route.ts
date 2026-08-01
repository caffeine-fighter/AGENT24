export const runtime = "edge";

export async function GET() {
  return Response.json({
    status: "ok",
    mode: process.env.OPENAI_API_KEY?.trim() ? "openai_hosted" : "offline_demo",
    openai_configured: Boolean(process.env.OPENAI_API_KEY?.trim()),
    github_configured: Boolean(process.env.GITHUB_TOKEN?.trim()),
    sdk: "responses-api",
    safety_boundary: "SIMULATION_ONLY",
  });
}
