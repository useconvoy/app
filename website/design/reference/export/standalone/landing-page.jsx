const DS = window.ConvoyDesignSystem_29acff;
const { Button, ActionGroup, SiteHeader, SiteFooter, SkipLink, PageShell, Section, SectionHeading, DependencyRows, WorkflowSteps, Disclosure, ReleaseComposition, ExecutionPath, PartnershipPanel, ContactEmailPanel } = DS;

// Single place to change the receiver.
const CONTACT_EMAIL = "aws@deployconvoy.com";

const NAV = [{ label: "How it works", href: "#how" }, { label: "Design partnership", href: "#partners" }, { label: "Contact", href: "#contact" }];
const CTA = { label: "Discuss your deployment", href: "#contact" };

function Hero() {
  return (
    <Section rule={false} labelledBy="hero-title" className="lp-hero">
      <div className="lp-hero__grid">
        <div className="lp-hero__copy">
          <p className="cv-eyebrow">Deployment infrastructure for physical AI</p>
          <h1 id="hero-title" className="cv-h1">Deploy AI models to real robots.</h1>
          <p className="cv-lead cv-prose">Convoy is building the runtime and release workflow that connects trained models to robot sensors, compute, and controllers, starting with learned manipulation.</p>
          <ActionGroup><Button arrow href="#contact">Discuss your deployment</Button><Button variant="secondary" href="#how">Explore the workflow</Button></ActionGroup>
          <p className="cv-small lp-hero__invite">Help shape the next robot deployment workflow.</p>
        </div>
        <div className="lp-hero__figure"><ReleaseComposition /></div>
      </div>
    </Section>
  );
}

function Gap() {
  return (
    <Section labelledBy="gap-title">
      <SectionHeading id="gap-title" eyebrow="The gap" title="A trained model is not a robot deployment." lead="A model can learn a task without carrying everything a robot needs to perform it. The camera inputs must match. The actions must make sense to the controller. The runtime must fit the compute. And the resulting system needs to be evaluated for the task. When the model changes, those assumptions can change with it." />
      <div className="cv-section__block">
        <DependencyRows columns={["", "What the deployment must carry"]} rows={[
          { term: "Inputs", body: "Images, sensor data, and robot state prepared the way the model expects." },
          { term: "Actions", body: "Model outputs translated into the units, coordinates, and commands the controller understands." },
          { term: "Execution", body: "The runtime, dependencies, hardware settings, and timing required by the deployment." },
          { term: "Release evidence", body: "A record of the configuration, conditions, and checks used to evaluate the system." },
        ]} />
      </div>
    </Section>
  );
}

function How() {
  return (
    <Section id="how" labelledBy="how-title">
      <SectionHeading id="how-title" eyebrow="How it works" title="Package the system. Qualify the release." lead="We’re building a workflow around the complete robot-policy release." />
      <div className="cv-section__block">
        <WorkflowSteps steps={[
          { title: "Package", body: "Bring the model, input and action processing, runtime dependencies, and target configuration together." },
          { title: "Qualify", body: "Check that release against defined task and runtime criteria. Keep the conditions and results connected to the configuration that was tested." },
          { title: "Release", body: "Carry the identified configuration into deployment, with a clear basis for observing behavior and managing subsequent changes." },
        ]} />
      </div>
    </Section>
  );
}

function Path() {
  return (
    <Section labelledBy="path-title">
      <SectionHeading id="path-title" eyebrow="Runtime" title="Designed around your robot’s execution path." lead="Sensors feed input processing, the model produces actions, and action processing shapes them for the controller. Convoy’s runtime is being built to sit inside that path. The robot controller and safety system stay where they are." />
      <div className="cv-section__block"><ExecutionPath /></div>
    </Section>
  );
}

function Partners() {
  return (
    <Section id="partners" labelledBy="partners-title">
      <PartnershipPanel id="partners-title" title="Start with one model. One robot configuration. One clear deployment goal." lead="Each partnership starts with a defined model, robot configuration, and deployment goal. We’re working with teams on learned manipulation first." actions={<Button arrow href="#contact">Discuss your deployment</Button>} fit={[
        { label: "A good fit", body: "A trained manipulation policy you want running on hardware you already have." },
        { label: "A good fit", body: "A team that wants the release, its configuration, and its evidence kept together." },
        { label: "Not yet", body: "Fleet-wide rollout across many robot types, or training a model from scratch." },
      ]} />
    </Section>
  );
}

function Faq() {
  return (
    <Section labelledBy="faq-title" grid="split">
      <SectionHeading id="faq-title" eyebrow="Questions" title="Questions about Convoy." lead="Convoy is deliberately narrow. These answers describe what it is and is not." />
      <div>
        <Disclosure summary="Is Convoy a robot maker?">No. Convoy does not build robots or robot hardware. We’re building deployment infrastructure for models that run on robots you already have, working with the robot’s existing controller.</Disclosure>
        <Disclosure summary="Does Convoy train models?">No. You bring a trained model. Convoy is building the workflow to package, qualify, and release it onto a defined robot configuration.</Disclosure>
        <Disclosure summary="Does it support any model on any robot?">No. Compatibility and scope are defined around each deployment, starting with learned manipulation on specific robot configurations worked through with design partners.</Disclosure>
        <Disclosure summary="Is the cloud required?">No. Where input processing and the model run is part of the target configuration: on the robot, at the site, or in the cloud. Execution placement depends on the task’s compute, timing, and failure requirements.</Disclosure>
        <Disclosure summary="Does Convoy replace the robot controller or safety system?">No. The controller and safety system remain with the robot maker or integrator. Convoy is building the part that hands processed actions to the controller; it does not take over control or safety.</Disclosure>
        <Disclosure summary="Is this available today?">Convoy is in development. We’re speaking with robotics teams about focused design partnerships, with compatibility and scope defined around each deployment.</Disclosure>
      </div>
    </Section>
  );
}

function Contact() {
  return (
    <Section id="contact" labelledBy="contact-title" grid="split">
      <SectionHeading id="contact-title" eyebrow="Contact" title="Tell us about your next robot deployment." lead="Share your model, robot configuration, and the deployment challenge you’re working through." />
      <ContactEmailPanel email={CONTACT_EMAIL} />
    </Section>
  );
}

function LandingPage() {
  return (
    <>
      <SkipLink href="#main" />
      <PageShell header={<SiteHeader links={NAV} cta={CTA} />} footer={<SiteFooter links={NAV} description="Deployment infrastructure for physical AI." />}>
        <Hero /><Gap /><How /><Path /><Partners /><Faq /><Contact />
      </PageShell>
    </>
  );
}
Object.assign(window, { LandingPage, CONTACT_EMAIL });
