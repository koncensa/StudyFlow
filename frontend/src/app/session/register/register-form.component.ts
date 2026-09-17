import { Component, EventEmitter, Input, Output } from "@angular/core";

@Component({
  selector: "app-register-form",
  templateUrl: "./register-form.component.html",
  styleUrls: ["../session-fields.css"],
})
export class RegisterFormComponent {
  @Input() fullName = "";
  @Input() email = "";
  @Input() password = "";
  @Input() emailError = "";
  @Input() passwordError = "";

  @Output() fullNameChange = new EventEmitter<string>();
  @Output() emailChange = new EventEmitter<string>();
  @Output() passwordChange = new EventEmitter<string>();
}
