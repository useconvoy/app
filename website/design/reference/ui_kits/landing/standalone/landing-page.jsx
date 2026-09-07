const DS = window.ConvoyDesignSystem_29acff;
const { Button, TextField, TextArea, Checkbox, SiteHeader, SiteFooter, StatusBadge, SectionHeading, DependencyRows, WorkflowSteps, Disclosure, Notice, ReleaseEnvelope, ExecutionPath, DiagnosticPanel } = DS;

const NAV = [{ label: "How it works", href: "#how" }, { label: "Design partnership", href: "#partners" }, { label: "Contact", href: "#contact" }];
const CTA = { label: "Discuss your deployment", href: "#contact" };

function Hero() {
  return (
    <section className="lp-hero cv-container" aria-labelledby="hero-title">
      <div className="lp-hero__copy">
        <p className="cv-eyebrow">Deployment infrastructure for physical AI</p>
        <h1 id="hero-title" className="cv-h1">Deploy AI models to real robots.</h1>
        <p className="cv-lead cv-prose">Convoy is building the runtime and release workflow that connects trained models to robot sensors, compute, and controllers—starting with learned manipulation.</p>
        <div className="lp-hero__actions"><Button arrow href="#contact">Discuss your deployment</Button><Button variant="secondary" href="#how">Explore the workflow</Button></div>
        <div className="lp-hero__stage"><StatusBadge tone="info">Stage: In development</StatusBadge><p className="cv-small" style={{ color: "var(--color-text-secondary)" }}>We’re inviting robotics teams to help shape a focused deployment workflow.</p></div>
      </div>
      <div className="lp-hero__figure"><ReleaseEnvelope /></div>
    </section>
  );
}

function Gap() {
  return (
    <section className="cv-section cv-container" aria-labelledby="gap-title">
      <SectionHeading id="gap-title" eyebrow="The gap" title="A trained model is not a robot deployment." lead="A model can learn a task without carrying everything a robot needs to perform it. The camera inputs must match. The actions must make sense to the controller. The runtime must fit the compute. And the resulting system needs to be evaluated for the task. When the model changes, those assumptions can change with it." />
      <div className="lp-block">
        <DependencyRows columns={["", "What the deployment must carry"]} rows={[
          { term: "Inputs", body: "Images, sensor data, and robot state prepared the way the model expects." },
          { term: "Actions", body: "Model outputs translated into the units, coordinates, and commands the controller understands." },
          { term: "Execution", body: "The runtime, dependencies, hardware settings, and timing required by the deployment." },
          { term: "Release evidence", body: "A record of the configuration, conditions, and checks used to evaluate the system." },
        ]} />
      </div>
    </section>
  );
}

function How() {
  return (
    <section id="how" className="cv-section cv-container" aria-labelledby="how-title">
      <SectionHeading id="how-title" eyebrow="How it works" title="Package the system. Qualify the release." lead={<>A proposed three-step workflow. Convoy groups everything a deployment depends on into one versioned release envelope, then treats qualification as part of releasing rather than an afterthought. <StatusBadge tone="warning">Proposed workflow</StatusBadge></>} />
      <div className="lp-block">
        <WorkflowSteps steps={[
          { title: "Package", tag: "proposed", body: "Collect the model assets, input and action processing, runtime, and target configuration into a single versioned envelope.", items: ["Model assets", "Input and action processing", "Runtime", "Target configuration"] },
          { title: "Qualify", tag: "proposed", body: "Run the envelope against the target robot configuration and record what was checked. Evidence travels with the release.", items: ["Recorded test runs", "Checks passed or failed", "Sign-off before release"] },
          { title: "Release", tag: "proposed", body: "Promote a qualified envelope to the robot configuration it was qualified for. Roll back to a previous envelope when needed.", items: ["Versioned promotion", "Rollback to a known envelope", "Configuration-scoped"] },
        ]} />
      </div>
      <div className="lp-block lp-diag">
        <div className="lp-diag__copy cv-prose"><h3 className="cv-h3">What a release must carry</h3><p className="cv-body" style={{ color: "var(--color-text-secondary)" }}>The panel shows the kind of fields a release contract would hold. Names are conceptual and will change; the point is that a release is more than a model file.</p></div>
        <DiagnosticPanel />
      </div>
    </section>
  );
}

function Path() {
  return (
    <section className="cv-section cv-container" aria-labelledby="path-title">
      <SectionHeading id="path-title" eyebrow="Runtime" title="Designed around your robot’s execution path." lead="Sensors feed input processing, the model produces actions, action processing shapes them for the controller. Convoy’s runtime sits inside that path; the robot controller and safety system stay where they are." />
      <div className="lp-block"><ExecutionPath traceable /></div>
      <div className="lp-block lp-cols">
        <div><h3 className="cv-h3">Placement depends on configuration</h3><p className="cv-body" style={{ color: "var(--color-text-secondary)" }}>Input processing and the model may run on the robot, on a site machine, or in the cloud. The release envelope records which, so the same envelope is not silently run somewhere else.</p></div>
        <div><h3 className="cv-h3">Repeatable releases, not identical behaviour</h3><p className="cv-body" style={{ color: "var(--color-text-secondary)" }}>Convoy aims to make the release repeatable: the same assets, processing, runtime, and configuration every time. It does not guarantee identical physical behaviour, which depends on the robot, its environment, and its controller.</p></div>
      </div>
    </section>
  );
}

function Partners() {
  return (
    <section id="partners" className="cv-section cv-container" aria-labelledby="partners-title">
      <div className="lp-partner">
        <div>
          <SectionHeading id="partners-title" eyebrow="Design partnership" title="Start with one model. One robot configuration. One clear deployment goal." lead="We’re looking for a small number of design partners working on learned manipulation. Scoped compatibility comes first: a defined model, a defined robot configuration, and a deployment you need to make repeatable." />
          <div className="lp-partner__actions"><Button arrow href="#contact">Discuss your deployment</Button></div>
        </div>
        <ul className="lp-partner__fit">
          <li><span className="cv-eyebrow">A good fit</span><p>A trained manipulation policy you want running on hardware you already have.</p></li>
          <li><span className="cv-eyebrow">A good fit</span><p>A team that wants release evidence and rollback, not just a working demo.</p></li>
          <li><span className="cv-eyebrow">Not yet</span><p>Fleet-wide rollout across many robot types, or training a model from scratch.</p></li>
        </ul>
      </div>
    </section>
  );
}

function Boundaries() {
  return (
    <section className="cv-section cv-container" aria-labelledby="bounds-title">
      <div className="lp-faq">
        <SectionHeading id="bounds-title" eyebrow="Boundaries" title="A few important boundaries." lead="Convoy is deliberately narrow. These answers describe what it is and is not." />
        <div>
          <Disclosure name="faq" open summary="Is Convoy a robot maker?">No. Convoy does not build robots or robot hardware. It deploys trained models onto robots you already have, working with the robot’s existing controller.</Disclosure>
          <Disclosure name="faq" summary="Does Convoy train models?">No. Convoy is deployment infrastructure. You bring a trained model; Convoy is building the workflow to package, qualify, and release it onto a robot configuration.</Disclosure>
          <Disclosure name="faq" summary="Does it support any model on any robot?">No. Compatibility is scoped, starting with learned manipulation on specific robot configurations worked through with design partners. Broader support is a roadmap question, not a promise.</Disclosure>
          <Disclosure name="faq" summary="Is the cloud required?">No. Where input processing and the model run is part of the target configuration: on the robot, at the site, or in the cloud. Cloud is one option, not a requirement.</Disclosure>
          <Disclosure name="faq" summary="Does Convoy replace the robot controller or safety system?">No. The controller and safety system remain with the robot maker or integrator. Convoy hands processed actions to the controller; it does not take over control or safety.</Disclosure>
          <Disclosure name="faq" summary="Is this available today?">Convoy is in development. The workflow on this page is proposed and is being shaped with design partners. Nothing here should be read as a shipped product.</Disclosure>
        </div>
      </div>
    </section>
  );
}

function ContactForm() {
  const [values, setValues] = React.useState({ email: "", company: "", problem: "", name: "", hardware: "" });
  const [errors, setErrors] = React.useState({});
  const [attempted, setAttempted] = React.useState(false);
  const set = (k) => (e) => setValues({ ...values, [k]: e.target.value });
  const validate = () => {
    const e = {};
    if (!values.email.trim()) e.email = "Enter your work email.";
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(values.email)) e.email = "Enter a valid email address.";
    else if (/@(gmail|yahoo|hotmail|outlook|icloud)\./i.test(values.email)) e.email = "Use a work email address.";
    if (!values.company.trim()) e.company = "Tell us which company or team you’re with.";
    if (values.problem.trim().length < 20) e.problem = "Describe the deployment problem in a sentence or two.";
    return e;
  };
  const onSubmit = (ev) => { ev.preventDefault(); const e = validate(); setErrors(e); setAttempted(true); };
  const count = Object.keys(errors).length;
  return (
    <form className="lp-form" onSubmit={onSubmit} noValidate aria-describedby="form-preview">
      <Notice tone="info" role="status"><span id="form-preview">Preview: this form is not connected. Nothing you type is sent or stored. The live site will use the verified repository configuration.</span></Notice>
      <div className="lp-form__grid">
        <TextField label="Work email" type="email" autoComplete="email" value={values.email} onChange={set("email")} error={errors.email} placeholder="you@company.com" />
        <TextField label="Company or team" autoComplete="organization" value={values.company} onChange={set("company")} error={errors.company} />
        <TextField label="Name" optional autoComplete="name" value={values.name} onChange={set("name")} />
        <TextField label="Model and hardware" optional value={values.hardware} onChange={set("hardware")} hint="Policy type, robot, compute — whatever you can share." />
      </div>
      <TextArea label="Deployment problem" value={values.problem} onChange={set("problem")} error={errors.problem} hint="What are you trying to get onto a robot, and what does done look like?" rows={5} />
      {attempted && count > 0 && <Notice tone="error">{count === 1 ? "Fix the highlighted field to continue." : `Fix the ${count} highlighted fields to continue.`}</Notice>}
      {attempted && count === 0 && <Notice tone="warning">Validation passed. In this preview, nothing is sent.</Notice>}
      <div className="lp-form__actions"><Button type="submit" arrow>Discuss your deployment</Button><span className="cv-small" style={{ color: "var(--color-text-muted)" }}>No mailing list. A person replies.</span></div>
    </form>
  );
}

function Contact() {
  return (
    <section id="contact" className="cv-section cv-container" aria-labelledby="contact-title">
      <div className="lp-contact">
        <SectionHeading id="contact-title" eyebrow="Contact" title="What does your next robot deployment need?" lead="Tell us about the model, the robot, and where things get stuck. If it fits the design partnership, we’ll set up a working session." />
        <ContactForm />
      </div>
    </section>
  );
}

function LandingPage() {
  return (
    <>
      <a className="lp-skip" href="#main">Skip to content</a>
      <SiteHeader links={NAV} cta={CTA} />
      <main id="main">
        <Hero /><Gap /><How /><Path /><Partners /><Boundaries /><Contact />
      </main>
      <SiteFooter links={NAV} description="Deployment infrastructure for physical AI. In development; workflow shown is proposed." />
    </>
  );
}
Object.assign(window, { LandingPage });
