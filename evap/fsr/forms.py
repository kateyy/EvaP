from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMessage
from django.forms.models import BaseInlineFormSet
from django.template import Context, Template
from django.utils.translation import ugettext_lazy as _

from evap.evaluation.models import Assignment, Course, Question, Questionnaire, \
                                   Semester, TextAnswer, UserProfile
from evap.fsr.models import EmailTemplate
from evap.fsr.fields import UserModelMultipleChoiceField, ToolTipModelMultipleChoiceField


class ImportForm(forms.Form):
    vote_start_date = forms.DateField(label=_(u"first date to vote"))
    vote_end_date = forms.DateField(label=_(u"last date to vote"))
    
    excel_file = forms.FileField(label=_(u"excel file"))
    
    def __init__(self, *args, **kwargs):
        super(ImportForm, self).__init__(*args, **kwargs)
        
        self.fields['vote_start_date'].localize = True
        self.fields['vote_start_date'].widget = forms.DateInput()
        
        self.fields['vote_end_date'].localize = True
        self.fields['vote_end_date'].widget = forms.DateInput()


class SemesterForm(forms.ModelForm):
    class Meta:
        model = Semester


class CourseForm(forms.ModelForm):
    general_questions = ToolTipModelMultipleChoiceField(required=False, queryset=Questionnaire.objects.filter(obsolete=False))
    
    class Meta:
        model = Course
        exclude = ("voters", "semester", "state")
    
    def __init__(self, *args, **kwargs):
        super(CourseForm, self).__init__(*args, **kwargs)
        self.fields['participants'] = UserModelMultipleChoiceField(queryset=User.objects.order_by("last_name", "username"))
        
        if self.instance.general_assignment:
            self.fields['general_questions'].initial = [q.pk for q in self.instance.general_assignment.questionnaires.all()]
        
        self.fields['vote_start_date'].localize = True
        self.fields['vote_start_date'].widget = forms.DateInput()
        if self.instance.state == "inEvaluation":
            self.fields['vote_start_date'].required = False
            self.fields['vote_start_date'].widget.attrs['disabled'] = True
        
        self.fields['vote_end_date'].localize = True
        self.fields['vote_end_date'].widget = forms.DateInput()
        
        self.fields['kind'].widget = forms.Select(choices=[(a, a) for a in Course.objects.values_list('kind', flat=True).order_by().distinct()])
        self.fields['study'].widget = forms.Select(choices=[(a, a) for a in Course.objects.values_list('study', flat=True).order_by().distinct()])
    
    def save(self, *args, **kw):
        super(CourseForm, self).save(*args, **kw)
        self.instance.general_assignment.questionnaires = self.cleaned_data.get('general_questions')
        self.instance.save()


class AssignmentForm(forms.ModelForm):
    class Meta:
        model = Assignment
    
    def __init__(self, *args, **kwargs):
        super(AssignmentForm, self).__init__(*args, **kwargs)
        self.fields['lecturer'].queryset = User.objects.order_by("last_name", "username")


class CourseEmailForm(forms.Form):
    sendToParticipants = forms.BooleanField(label=_("Send to participants?"), required=False, initial=True)
    sendToLecturers = forms.BooleanField(label=_("Send to lecturers?"), required=False)
    subject = forms.CharField(label=_("Subject"))
    body = forms.CharField(widget=forms.Textarea(), label=_("Body"))
    
    def __init__(self, *args, **kwargs):
        self.instance = kwargs.pop('instance')
        self.template = EmailTemplate()
        super(CourseEmailForm, self).__init__(*args, **kwargs)
    
    def clean(self):
        cleaned_data = self.cleaned_data
        
        if not (cleaned_data.get('sendToParticipants') or cleaned_data.get('sendToLecturers')):
            raise forms.ValidationError(_(u"No recipient selected. Choose at least participants or lecturers."))
        
        return cleaned_data

    # returns whether all recepients have an email address
    def all_recepients_reachable(self):
        return self.missing_email_addresses() == 0
    
    # returns the number of recepients without an email address
    def missing_email_addresses(self):
        return len(list(self.template.receipient_list_for_course(self.instance, self.cleaned_data.get('sendToLecturers'), self.cleaned_data.get('sendToParticipants'))))
    
    def send(self):
        self.template.subject = self.cleaned_data.get('subject')
        self.template.body = self.cleaned_data.get('body')
        self.template.send_courses([self.instance], self.cleaned_data.get('sendToLecturers'), self.cleaned_data.get('sendToParticipants'))

class QuestionnaireForm(forms.ModelForm):
    class Meta:
        model = Questionnaire


class CensorTextAnswerForm(forms.ModelForm):
    ACTION_CHOICES = (
        (u"1", _(u"Approved")),
        (u"2", _(u"Censored")),
        (u"3", _(u"Hide")),
        (u"4", _(u"Needs further review")),
    )

    class Meta:
        model = TextAnswer
        fields = ('censored_answer',)
    
    def __init__(self, *args, **kwargs):
        super(CensorTextAnswerForm, self).__init__(*args, **kwargs)
        self.fields['action'] = forms.TypedChoiceField(widget=forms.RadioSelect(), choices=self.ACTION_CHOICES, coerce=int)
    
    def clean(self):
        cleaned_data = self.cleaned_data
        action = cleaned_data.get("action")
        censored_answer = cleaned_data.get("censored_answer")
        
        if action == 2 and not censored_answer:
            raise forms.ValidationError(_(u'Censored answer missing.'))
        
        return cleaned_data


class AtLeastOneFormSet(BaseInlineFormSet):
    def is_valid(self):
        return super(AtLeastOneFormSet, self).is_valid() and not any([bool(e) for e in self.errors])
    
    def clean(self):
        # get forms that actually have valid data
        count = 0
        for form in self.forms:
            try:
                if form.cleaned_data and not form.cleaned_data.get('DELETE', False):
                    count += 1
            except AttributeError:
                # annoyingly, if a subform is invalid Django explicity raises
                # an AttributeError for cleaned_data
                pass
        
        if count < 1:
            raise forms.ValidationError(_(u'You must have at least one of these.'))

class LecturerFormSet(AtLeastOneFormSet):
    def clean(self):
        super(LecturerFormSet, self).clean()
        
        found_lecturer = []
        for form in self.forms:
            try:
                if form.cleaned_data:
                    lecturer = form.cleaned_data.get('lecturer')
                    if lecturer and lecturer in found_lecturer:
                        raise forms.ValidationError(_(u'Duplicate lecturer found. Each lecturer should only be used once'))
                    elif lecturer:
                        found_lecturer.append(lecturer)
            except AttributeError:
                # annoyingly, if a subform is invalid Django explicity raises
                # an AttributeError for cleaned_data
                pass

class IdLessQuestionFormSet(AtLeastOneFormSet):
    class PseudoQuerySet(list):
        db = None
    
    def __init__(self, data=None, files=None, instance=None, save_as_new=False, prefix=None, queryset=None):
        self.save_as_new = save_as_new
        self.instance = instance
        super(BaseInlineFormSet, self).__init__(data, files, prefix=prefix, queryset=queryset)
    
    def get_queryset(self):
        if not hasattr(self, '_queryset'):
            self._queryset = IdLessQuestionFormSet.PseudoQuerySet()
            self._queryset.extend([Question(text_de=e.text_de, text_en=e.text_en, kind=e.kind) for e in self.queryset.all()])
            self._queryset.db = self.queryset.db
        return self._queryset


class QuestionForm(forms.ModelForm):
    class Meta:
        model = Question
    
    def __init__(self, *args, **kwargs):
        super(QuestionForm, self).__init__(*args, **kwargs)
        self.fields['text_de'].widget = forms.TextInput()
        self.fields['text_en'].widget = forms.TextInput()


class QuestionnairesAssignForm(forms.Form):
    def __init__(self, *args, **kwargs):
        semester = kwargs.pop('semester')
        extras = kwargs.pop('extras', ())
        super(QuestionnairesAssignForm, self).__init__(*args, **kwargs)
        
        # course kinds
        for kind in semester.course_set.values_list('kind', flat=True).order_by().distinct():
            self.fields[kind] = ToolTipModelMultipleChoiceField(required=False, queryset=Questionnaire.objects.filter(obsolete=False))
        
        # extra kinds
        for extra in extras:
            self.fields[extra] = ToolTipModelMultipleChoiceField(required=False, queryset=Questionnaire.objects.filter(obsolete=False))


class SelectCourseForm(forms.Form):
    def __init__(self, queryset, *args, **kwargs):
        super(SelectCourseForm, self).__init__(*args, **kwargs)
        self.queryset = queryset
        self.selected_courses = []
        
        for course in self.queryset:
            self.fields[str(course.id)] = forms.BooleanField(label=course.name, required=False)
    
    def clean(self):
        cleaned_data = self.cleaned_data
        for id, selected in cleaned_data.iteritems():
            if selected:
                self.selected_courses.append(Course.objects.get(pk=id))
        return cleaned_data

    
class UserForm(forms.ModelForm):
    username = forms.CharField()
    first_name = forms.CharField()
    last_name = forms.CharField()
    email = forms.CharField(required=False)
    fsr = forms.BooleanField(required=False, label=_("FSR Member"))
    proxies = UserModelMultipleChoiceField(queryset=User.objects.order_by("last_name", "username"))
    
    class Meta:
        model = UserProfile
        exclude = ('user',)
        fields = ['username', 'title', 'first_name', 'last_name', 'email', 'picture', 'proxies', 'is_lecturer']
    
    def __init__(self, *args, **kwargs):
        super(UserForm, self).__init__(*args, **kwargs)
        
        # fix generated form
        self.fields['proxies'].required = False
        
        # load user fields
        self.fields['username'].initial = self.instance.user.username
        self.fields['first_name'].initial = self.instance.user.first_name
        self.fields['last_name'].initial = self.instance.user.last_name
        self.fields['email'].initial = self.instance.user.email
        self.fields['fsr'].initial = self.instance.user.is_staff

    def save(self, *args, **kw):
        # first save the user, so that the profile gets created for sure
        self.instance.user.username = self.cleaned_data.get('username')
        self.instance.user.first_name = self.cleaned_data.get('first_name')
        self.instance.user.last_name = self.cleaned_data.get('last_name')
        self.instance.user.email = self.cleaned_data.get('email')
        self.instance.user.is_staff = self.cleaned_data.get('fsr')
        self.instance.user.save()
        self.instance = self.instance.user.get_profile()
        
        super(UserForm, self).save(*args, **kw)


class LotteryForm(forms.Form):
    number_of_winners = forms.IntegerField(label=_(u"Number of Winners"), initial=3)


class EmailTemplateForm(forms.ModelForm):
    class Meta:
        model = EmailTemplate
        exclude = ("name", )
