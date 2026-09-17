import { Component, EventEmitter, Input, Output } from "@angular/core";

@Component({
  selector: "app-login-form",
  templateUrl: "./login-form.component.html",
  styleUrls: ["../session-fields.css"],
})
export class LoginFormComponent {
  @Input() email = "";
  @Input() password = "";
  @Input() emailError = "";
  @Input() passwordError = "";

  @Output() emailChange = new EventEmitter<string>();
  @Output() passwordChange = new EventEmitter<string>();
}
