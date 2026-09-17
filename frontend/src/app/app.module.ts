import { BrowserModule } from "@angular/platform-browser";
import { NgModule } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { HTTP_INTERCEPTORS, HttpClientModule } from "@angular/common/http";
import { ChartsModule } from "ng2-charts";
import { AppComponent } from "./app.component";
import { PdfAssistantComponent } from "./components/pdf-assistant/pdf-assistant.component";
import { QuizComponent } from "./components/quiz/quiz.component";
import { ResultsComponent } from "./components/results/results.component";
import { PomodoroComponent } from "./components/pomodoro/pomodoro.component";
import { PomodoroWidgetComponent } from "./components/pomodoro/pomodoro-widget.component";
import { StudyAnalysisComponent } from "./components/study-analysis/study-analysis.component";
import { AcademicPlanComponent } from "./components/academic-plan/academic-plan.component";
import { HomeComponent } from "./components/home/home.component";
import { SessionHeaderComponent } from "./session/session-header.component";
import { SessionDialogComponent } from "./session/session-dialog.component";
import { LoginFormComponent } from "./session/login/login-form.component";
import { RegisterFormComponent } from "./session/register/register-form.component";
import { UserProfileComponent } from "./components/user-profile/user-profile.component";
import { LangPipe } from "./i18n/lang.pipe";
import { AssistantHtmlPipe } from "./pipes/assistant-html.pipe";
import { ApiTokenInterceptor } from "./session/api-token.interceptor";

@NgModule({
  declarations: [
    AppComponent,
    PdfAssistantComponent,
    QuizComponent,
    ResultsComponent,
    PomodoroComponent,
    PomodoroWidgetComponent,
    StudyAnalysisComponent,
    AcademicPlanComponent,
    HomeComponent,
    SessionHeaderComponent,
    SessionDialogComponent,
    LoginFormComponent,
    RegisterFormComponent,
    UserProfileComponent,
    AssistantHtmlPipe,
    LangPipe,
  ],
  imports: [BrowserModule, FormsModule, HttpClientModule, ChartsModule],
  providers: [{ provide: HTTP_INTERCEPTORS, useClass: ApiTokenInterceptor, multi: true }],
  bootstrap: [AppComponent],
})
export class AppModule {}
