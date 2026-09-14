import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** The shadcn class helper the AI Elements components expect. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
