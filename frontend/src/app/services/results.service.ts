import { Injectable } from "@angular/core";
import { QuizResultsPageResponse } from "../models/types";
import { normalizeQuizResultsPageResponse } from "../utils/quiz-submission-normalize";
import { ApiService } from "./api.service";

@Injectable({
  providedIn: "root",
})
export class ResultsService {
  constructor(private api: ApiService) {}

  async getUserResults(locale: "en" | "tr"): Promise<QuizResultsPageResponse | null> {
    const raw = await this.api.getQuizUserResults(locale).toPromise();
    return normalizeQuizResultsPageResponse(raw);
  }
}
