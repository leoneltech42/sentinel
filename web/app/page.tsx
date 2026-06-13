import { redirect } from "next/navigation";

export default function Home() {
  const today = new Date().toISOString().split("T")[0];
  redirect(`/date/${today}`);
}
